"""Generic persistence layer for DAG nodes.

Stores versioned source values and resolved derived values in SQLite.
Serialises complex types via TypeAdapter with ``_type``/``_module`` markers.
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import logging
import sqlite3
import threading
import zlib
from collections.abc import Collection, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal as _Decimal
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, cast, override

from money import Money as _Money
from pint import Quantity
from pydantic import TypeAdapter

logger = logging.getLogger(__name__)


DB_PATH: Path | None = None
testing: bool = False
_connection_cache = threading.local()


@dataclasses.dataclass(frozen=True)
class WireRecord(Mapping[str, object]):
    """A record whose fields ARE its wire shape.

    The node value dicts were already records — a fixed set of keys and no
    behaviour — but the shape only existed in the literal that built it, so
    nothing could name it, type it or check it. A class gives the shape a name
    and its fields types.

    It still reads like the dict it replaces (``value["rid"]`` as well as
    ``value.rid``): the readers of a node value are all over the codebase and
    changing them to attribute access would be a second, needless migration.
    ``DagJSONEncoder`` writes ``dataclasses.asdict``, so what reaches the
    database and the frontend is byte-identical to the literal's JSON.
    """

    @override
    def __getitem__(self, key: str) -> object:
        try:
            return getattr(self, key)
        except AttributeError as exc:
            raise KeyError(key) from exc

    @override
    def __iter__(self) -> Iterator[str]:
        return iter(self.__dataclass_fields__)

    @override
    def __len__(self) -> int:
        return len(self.__dataclass_fields__)


class DagJSONEncoder(json.JSONEncoder):
    """Handles enums, Decimal, Money, Quantity, and other non-serializable types in DAG node results."""

    @override
    def default(self, o):
        if isinstance(o, Enum):
            return o.name.lower()
        if isinstance(o, _Decimal):
            return float(o)
        if isinstance(o, _Money):
            # lucidlint: ignore record-shape wire-format dict — serialization boundary
            return {"amount": str(o.amount), "currency": o.currency}
        if isinstance(o, cast(type, Quantity)):
            m = float(o.magnitude)
            # lucidlint: ignore record-shape wire-format dict — serialization boundary
            return {"value": int(m) if m == int(m) else m, "unit": str(o.units)}
        if isinstance(o, WireRecord):
            # A wire record's JSON is its fields (recursively), so storing one
            # writes exactly what the dict literal it replaced wrote.
            return dataclasses.asdict(o)
        return super().default(o)


_ZLIB_LEVEL = 6
"""zlib level for the provenance blob.  Level 6 vs 9 trades ~2% ratio for
~4x faster compress — the money-cascade provenance trees (the bulk of a
row's bytes) compress ~25x either way."""

PROVENANCE_KEY = "provenance"
"""The one record field that keeps compression.

Every other part of a node record is plain text in its own column, so the hot
readers (attempt load, staleness checks, the listing) parse only what they use
and never inflate a provenance tree — those trees are the bulk of the table
(1.53 GB live, mostly the money cascades' expansions) and only the provenance
endpoint reads them.
"""

_SPLIT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("status", "TEXT"),
    ("value_json", "TEXT"),
    ("error", "TEXT"),
    ("error_detail_json", "TEXT"),
    ("source_url", "TEXT"),
    ("source_label", "TEXT"),
    ("provenance_z", "BLOB"),
    ("extra_json", "TEXT"),
)
"""The per-field columns that replace the single ``result_json`` blob.

``source_label`` is its own column because every user-input load reads it
(``_load_persisted_label``); ``extra_json`` is the catch-all for any other
node-specific record field, so the split can never drop one.
"""

_PLAIN_COLUMN_KEYS: tuple[str, ...] = ("status", "error", "source_url", "source_label")
"""Record fields stored verbatim as their own TEXT column."""

_split_ensured: set[str] = set()

def _reset_caches() -> None:
    """Drop the per-database schema caches (test isolation).

    A test can recreate a database under the same path with a different
    schema (a pre-split table to prove legacy rows still read); the caches
    are keyed by path, so they must be dropped with it.
    """
    with _column_cache_lock:
        _split_ensured.clear()
        global _last_columns_conn, _last_columns
        _last_columns_conn = None
        _last_columns = ()


def compress_result(text: str) -> bytes:
    """zlib-compress a payload (the provenance blob) for storage."""
    return zlib.compress(text.encode("utf-8"), _ZLIB_LEVEL)


def decompress_result(raw: str | bytes) -> str:
    if isinstance(raw, bytes) and raw[:1] == b"\x78":
        try:
            return zlib.decompress(raw).decode("utf-8")
        except (zlib.error, UnicodeDecodeError) as exc:
            # A blob that starts like zlib but is not (a truncated write, a
            # foreign write). Report it as data damage: zlib's own message
            # ("incorrect header check") names neither the field nor the fix.
            raise ValueError(
                f"provenance blob is not readable as zlib-compressed JSON: {exc}"
            ) from exc
    if isinstance(raw, bytes):
        # Degenerate: a BLOB that is not a zlib stream.  JSON text never
        # starts with 0x78, so this only happens with a foreign write.
        return raw.decode("utf-8")
    return raw


def _record_columns() -> tuple[str, ...]:
    """The columns this database's node_results actually has (cached).

    A migrated or fresh database has the split columns and no ``result_json``;
    an older one still carries the legacy blob column (NOT NULL), so writes
    mirror the provenance-free record there until the shipped migration
    converts it.
    """
    cached = _table_columns(_get_db())
    return cached


def ensure_split_columns(conn: sqlite3.Connection) -> None:
    """Idempotently add the split columns to an older ``node_results``.

    Cheap ALTERs.  ``init_db`` calls this for the app's own connection, and the
    shipped migration calls it with the connection it is working on — one
    implementation of the DDL, so a database cannot end up with half the
    columns under either entry point.
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(node_results)")}
    added = [
        (column, column_type)
        for column, column_type in _SPLIT_COLUMNS
        if column not in existing
    ]
    for column, column_type in added:
        conn.execute(f"ALTER TABLE node_results ADD COLUMN {column} {column_type}")
    conn.commit()
    if added:
        # The DDL owns its own invalidation: a caller that read the columns
        # BEFORE this call must not keep answering with the old shape (the
        # review found this; the shipped migration happens to call us first,
        # the class did not go away — tests/unit/dag/test_persistence.py pins
        # it). Only when something was ALTERed: no ALTER, same shape, same
        # answer, and the cache stays valid.
        _invalidate_column_cache()


def _ensure_split_columns() -> None:
    """``ensure_split_columns`` for the app's connection, cached per DB path
    (the same pattern as the code_version column) — the hottest read path must
    not PRAGMA on every call."""
    key = str(DB_PATH)
    # The check-and-add is a unit: two threads passing the check would both
    # run ensure_split_columns and the second ALTER would fail on a duplicate
    # column name. The DDL sits in the same lock; a few milliseconds at
    # startup, on the once-per-process path.
    with _column_cache_lock:
        if key in _split_ensured:
            return
        ensure_split_columns(_get_db())  # invalidates the column cache itself
        _split_ensured.add(key)


@dataclasses.dataclass(frozen=True)
class StoredColumns:
    """One record's fields as the storage columns take them.

    A named record rather than a dict: the column names ARE the storage
    contract, and every writer (the app's persist, the shipped migration)
    asks this object for its values in the database's own column order, so
    none of them can name a column the schema does not have.
    """

    status: str | None
    value_json: str | None
    error: str | None
    error_detail_json: str | None
    source_url: str | None
    source_label: str | None
    provenance_z: bytes | None
    extra_json: str | None
    result_json: bytes

    filled: frozenset[str] = frozenset()
    """The columns THIS record's keys populated.

    Not the same as "writable": a writer that deliberately clears a field
    (an error that no longer applies) passes None and means it. The migration
    needs the distinction — see ``filled_columns``.
    """

    ORDER: ClassVar[tuple[str, ...]] = (
        "status",
        "value_json",
        "error",
        "error_detail_json",
        "source_url",
        "source_label",
        "provenance_z",
        "extra_json",
        "result_json",
    )

    def for_columns(self, columns: Sequence[str]) -> tuple[object, ...]:
        """The values in the caller's column order."""
        return tuple(getattr(self, column) for column in columns)

    def filled_columns(self, present: Collection[str], *, include_blob: bool = True) -> tuple[str, ...]:
        """The writable columns this record actually POPULATES.

        The migration needs this and nothing else does. A row the new code
        wrote while the legacy column still existed keeps its provenance tree
        in ``provenance_z`` and a mirror blob WITHOUT ``provenance`` (the
        mirror is deliberately provenance-free); converting that row from its
        blob would otherwise write NULL over the only copy of the tree. For a
        pre-split row the blob carries everything, so this writes the same
        columns as before.
        """
        return tuple(
            column
            for column in self.writable_columns(present, include_blob=include_blob)
            if column in self.filled
        )

    def writable_columns(self, present: Collection[str], *, include_blob: bool = True) -> tuple[str, ...]:
        """The columns of this record's shape that *present* has, in order.

        ``include_blob=False`` leaves ``result_json`` alone: it holds the
        legacy original until the migration drops it, so a half-converted
        database still reads with the previous artifact.
        """
        return tuple(
            column
            for column in self.ORDER
            if column in present and (include_blob or column != "result_json")
        )


# lucidlint: ignore record-shape the record's keys vary per node type (stored wire shape)
def record_columns(record: dict[str, Any]) -> StoredColumns:
    """The record's fields as storage column values — provenance compressed."""
    provenance = record.get(PROVENANCE_KEY)
    mapped = {*_PLAIN_COLUMN_KEYS, "value", "error_detail", PROVENANCE_KEY}
    extra = {k: v for k, v in record.items() if k not in mapped}
    plain = {key: record.get(key) for key in _PLAIN_COLUMN_KEYS}
    filled = frozenset(
        column
        for column, present in (
            ("status", "status" in record),
            ("value_json", "value" in record),
            ("error", "error" in record),
            ("error_detail_json", "error_detail" in record),
            ("source_url", "source_url" in record),
            ("source_label", "source_label" in record),
            ("provenance_z", provenance is not None),
            ("extra_json", bool(extra)),
            ("result_json", True),
        )
        if present
    )
    return StoredColumns(
        filled=filled,
        status=plain["status"],
        value_json=json.dumps(record["value"], cls=DagJSONEncoder) if "value" in record else None,
        error=plain["error"],
        error_detail_json=(
            json.dumps(record["error_detail"], cls=DagJSONEncoder)
            if record.get("error_detail") is not None
            else None
        ),
        source_url=plain["source_url"],
        source_label=plain["source_label"],
        provenance_z=(
            compress_result(json.dumps(provenance, cls=DagJSONEncoder)) if provenance is not None else None
        ),
        extra_json=json.dumps(extra, cls=DagJSONEncoder) if extra else None,
        # Legacy databases keep a NOT NULL result_json: mirror the record
        # WITHOUT provenance there (small, and enough for a rollback to read).
        # zlib, like every legacy row: the previous artifact's reader calls
        # zlib.decompress on this column, so a plain-JSON mirror would make a
        # rollback crash on every row the new code wrote.
        result_json=compress_result(
            json.dumps({k: v for k, v in record.items() if k != PROVENANCE_KEY}, cls=DagJSONEncoder)
        ),
    )


# lucidlint: ignore record-shape the legacy record IS the stored wire shape (keys vary per node)
def _read_legacy_record(raw: str | bytes) -> dict[str, Any]:
    """Parse a pre-split ``result_json`` value (whole-row zlib or plain JSON)."""
    return cast(dict[str, Any], json.loads(decompress_result(raw)))


# lucidlint: ignore record-shape the record's keys vary per node type (stored wire shape)
def read_node_record(row: sqlite3.Row) -> dict[str, Any]:
    """A row's record from the split columns (legacy blob for pre-split rows).

    Never inflates provenance — that is ``latest_node_provenance``'s job.
    """
    keys = row.keys()
    if "status" not in keys or (row["status"] is None and "result_json" in keys and row["result_json"]):
        legacy = _read_legacy_record(row["result_json"])
        legacy.pop(PROVENANCE_KEY, None)
        return legacy
    status = row["status"]
    # Exactly the fields the record carried: the succeeded/pending/impossible
    # flags are NOT synthesised from status — rows that never carried them
    # (user-input pushes) must read back without them, or the split would be a
    # data change (verified against the live database: 9 of 1542 sampled rows
    # gained flags they never had). Whoever needs a flag derives it.
    record: dict[str, Any] = {"status": status}
    if row["value_json"] is not None:
        record["value"] = json.loads(row["value_json"])
    if row["error"] is not None:
        record["error"] = row["error"]
    if row["error_detail_json"] is not None:
        record["error_detail"] = json.loads(row["error_detail_json"])
    record.update(
        {
            column: row[column]
            for column in ("source_url", "source_label")
            if column in keys and row[column] is not None
        }
    )
    if "extra_json" in keys and row["extra_json"] is not None:
        extra = json.loads(row["extra_json"])
        if isinstance(extra, dict):
            record.update(extra)
    return record


_column_cache_lock = threading.RLock()
"""`_get_db` hands each thread its own connection, so the column cache (and the
split-ensured set) is touched concurrently: the connection and its column list
are written as a PAIR and must not tear."""

_last_columns_conn: sqlite3.Connection | None = None
_last_columns: tuple[str, ...] = ()
"""The most recent connection's column list.

``_table_columns`` runs on the write path, and a migration calls it once per
row (the person-id backfill writes tens of thousands of rows through
``write_node_record``): a PRAGMA per row is measurable.  Keyed by the
connection OBJECT (never ``id()``, which the interpreter reuses after a
connection is collected): holding the reference is what makes the ``is``
comparison sound — a cached connection cannot be collected, so no other object
can ever sit at a recycled address and pass the check.  Invalidated by
``_reset_caches``, by ``init_db`` on a path change, and by the DDL that changes
the answer (``ensure_split_columns``).
"""


def _invalidate_column_cache() -> None:
    """Drop the cached column list — call after ANY DDL on node_results."""
    global _last_columns_conn, _last_columns
    with _column_cache_lock:
        _last_columns_conn = None
        _last_columns = ()


def _table_columns(conn: sqlite3.Connection) -> tuple[str, ...]:
    global _last_columns_conn, _last_columns
    with _column_cache_lock:
        if conn is _last_columns_conn:
            return _last_columns
        columns = tuple(row[1] for row in conn.execute("PRAGMA table_info(node_results)"))
        _last_columns_conn = conn
        _last_columns = columns
        return columns



def record_select_columns(conn: sqlite3.Connection) -> tuple[str, ...]:
    """The record columns to SELECT when decoding rows with :func:`read_node_record`.

    Whichever shape the database has: the per-field columns (minus the
    provenance blob, which the reader never inflates) plus the legacy
    ``result_json`` while it is still there.
    """
    existing = _table_columns(conn)
    wanted = (*(column for column, _ in _SPLIT_COLUMNS if column != "provenance_z"), "result_json")
    return tuple(column for column in wanted if column in existing)


# lucidlint: ignore record-shape the record's keys vary per node type (stored wire shape)
def write_node_record(conn: sqlite3.Connection, row_id: int, record: dict[str, Any]) -> None:
    """Rewrite one ``node_results`` row from *record*.

    Writes the split columns when this database has them, and the legacy
    ``result_json`` blob when it does not — the caller never names a storage
    column.  The legacy blob keeps its zlib encoding and its role as the
    source of truth until scripts/split_node_results.py converts the row, so a
    re-key written only into the split columns is not undone at the split.
    """
    present = _table_columns(conn)
    values = record_columns(record)
    columns = values.writable_columns(set(present), include_blob="result_json" in present)
    assignments = ", ".join(f"{column}=?" for column in columns)
    conn.execute(
        f"UPDATE node_results SET {assignments} WHERE rowid=?",
        (*values.for_columns(columns), row_id),
    )


def _get_db() -> sqlite3.Connection:
    global DB_PATH
    if DB_PATH is None:
        DB_PATH = Path("data/houses.db")

    if hasattr(_connection_cache, "conn"):
        return _connection_cache.conn

    if testing:
        raise RuntimeError(
            f"Refusing to open production DB at {DB_PATH} — test fixture "
            "should have replaced _get_db with an in-memory connection. "
            "Did a direct import bypass the replacement?"
        )

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _connection_cache.conn = conn
    return conn


def close_db() -> None:
    """Close the cached database connection and clear it."""
    if hasattr(_connection_cache, "conn"):
        _connection_cache.conn.close()
        _connection_cache.conn = None


# lucidlint: ignore unused deliberate test seam — round-trip primitives driven directly by test_persistence.py;
def _serialize_value(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, bool):
        return json.dumps(val)
    if isinstance(val, str):
        if not val:
            return ""
        return json.dumps(val)
    if isinstance(val, (int, float)):
        return str(val)
    try:
        ta = TypeAdapter(type(val))
        d = ta.dump_python(val)
        if isinstance(d, dict):
            d["_type"] = type(val).__name__
            d["_module"] = type(val).__module__
        return json.dumps(d)
    # lucidlint: ignore broad-except serialization failure logs the type then re-raises — never persists silently
    except Exception:
        logger.exception("Failed to serialize %s", type(val).__name__)
        raise


# lucidlint: ignore unused deliberate test seam — round-trip primitives driven directly by test_persistence.py;
def _deserialize_value(raw: str | None) -> Any:
    if raw is None:  # was: if not raw — empty string "" should not be treated as None
        return None
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return raw
    if isinstance(d, dict) and "_type" in d and "_module" in d:
        try:
            mod = importlib.import_module(d["_module"])
            cls = getattr(mod, d["_type"])
            fields = {k: v for k, v in d.items() if not k.startswith("_")}
            return cls(**fields)
        # lucidlint: ignore broad-except deserialization failure logs the _type then re-raises
        except Exception:
            logger.exception("Failed to deserialize %s", d.get("_type", "unknown"))
            raise
    return d


_code_version_ensured: set[str] = set()


def _ensure_code_version_column() -> None:
    """Idempotently add the ``code_version`` column to node_results.

    Older databases predate the code-version stamp; ALTER is cheap and
    safe (SQLite), and rows written before the column exists get NULL,
    which the staleness check treats as "unknown code" → one recompute.
    Cached per DB path — the PRAGMA was running on EVERY persist/load
    (the hottest persistence path) before the review caught it.
    """
    key = str(DB_PATH)
    if key in _code_version_ensured:
        return
    conn = _get_db()
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(node_results)")]
    if "code_version" not in cols:
        conn.execute("ALTER TABLE node_results ADD COLUMN code_version TEXT")
        conn.commit()
    _code_version_ensured.add(key)


def init_db(db_path: str | None = None) -> None:
    """Initialise the SQLite database schema, migrating older databases."""
    global DB_PATH
    if db_path:
        DB_PATH = Path(db_path)
    # Startup is the one moment the caches can be dropped for free, and the
    # file at a path can have been REPLACED (a restore in place) — a cache
    # keyed by path alone would then serve the previous database's shape and
    # skip re-ensuring the split columns.
    _reset_caches()
    conn = _get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS node_results (
            node_id TEXT NOT NULL,
            status TEXT,
            value_json TEXT,
            error TEXT,
            error_detail_json TEXT,
            source_url TEXT,
            source_label TEXT,
            provenance_z BLOB,
            extra_json TEXT,
            dep_timestamps TEXT,
            created_at TEXT NOT NULL,
            code_version TEXT
        )
        """
    )
    # Latest-row lookups (latest_node_result, property_created_at) are the
    # hot path — the index was dropped in the code_version rewrite and a
    # fresh database must still get it.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nr_node ON node_results(node_id, created_at DESC);")
    conn.commit()
    _ensure_code_version_column()
    # Older databases still carry the single result_json blob: add the split
    # columns (cheap ALTERs) so the new write/read shape works before the
    # shipped migration converts the rows and drops the blob.
    _ensure_split_columns()


# lucidlint: ignore record-shape wire-format dict — serialization boundary
def save_node_result(
    node_id: str,
    result_dict: dict[str, Any],
    dep_timestamps: dict[str, str] | None = None,
    created_at: str | None = None,
    code_version: str | None = None,
) -> int:
    """Persist a node's to_json() output to the node_results table.

    Each call appends a new row. The most recent row is the current value.
    *created_at* should be the same value used by the caller's ``_db_created_at``
    so that the in-memory timestamp matches the DB column (avoids false staleness).
    When omitted, a fresh timestamp is generated (callers that don't care about
    round-trip consistency).  *code_version* fingerprints the compute code
    that produced the value — a persisted row whose version no longer matches
    the current compute is stale-in-code and must recompute.

    Single-writer enforcement: persistence runs on the DAG processor
    thread (or a single-threaded context — startup, tests, scripts);
    see docs/dag-library.md → 'Thread rules'.
    """
    # lucidlint: ignore inline-import cycle break — scheduler imports this module's writers at top
    from dag.scheduler import assert_mutation_allowed

    assert_mutation_allowed()
    if not _table_exists("node_results"):
        init_db()
    _ensure_code_version_column()
    _ensure_split_columns()
    conn = _get_db()
    now = created_at or datetime.now(UTC).isoformat()
    stored = record_columns(result_dict)
    writable = stored.writable_columns(set(_record_columns()))
    values = {
        "node_id": node_id,
        **dict(zip(writable, stored.for_columns(writable), strict=True)),
        "dep_timestamps": json.dumps(dep_timestamps, cls=DagJSONEncoder) if dep_timestamps else None,
        "created_at": now,
        "code_version": code_version,
    }
    columns = [c for c in ("node_id", *writable, "dep_timestamps", "created_at", "code_version") if c in values]
    cur = conn.execute(
        f"INSERT INTO node_results ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
        tuple(values[c] for c in columns),
    )
    conn.commit()
    rowid = cur.lastrowid
    return rowid if rowid is not None else 0


# lucidlint: ignore record-shape wire-format dict — serialization boundary
def latest_node_result(node_id: str) -> dict[str, Any] | None:
    """Return the most recent to_json() dict for a node, or None.

    Reads never block on writes and never need to flush: this reads
    committed DB state as-is (WAL snapshot; timestamp predicates exclude
    unwritten rows by construction — docs/dag-library.md → 'Thread
    rules').
    """
    return _fetch_latest_row(node_id)


# lucidlint: ignore record-shape wire-format dict — the stored node to_json() payload, serialization boundary (keys
# vary per node type; the _-prefixed metadata is added here, never in the node) (coding-standards.md)
def _fetch_latest_row(node_id: str, before: str | None = None) -> dict[str, Any] | None:
    """The row fetch shared by latest_node_result and node_result_before:
    read committed state as-is (WAL snapshot; the optional ``before``
    timestamp excludes unwritten rows by construction — thread rules)."""
    if not _table_exists("node_results"):
        init_db()
        return None
    _ensure_code_version_column()
    _ensure_split_columns()
    conn = _get_db()
    # provenance_z is deliberately NOT selected: its blob is the bulk of the
    # row and only latest_node_provenance inflates it.
    wanted = (
        *[c for c, _ in _SPLIT_COLUMNS if c != "provenance_z"],
        # Pre-split databases only: the fallback for rows without columns.
        "result_json",
        "dep_timestamps",
        "created_at",
        "code_version",
    )
    selected = ", ".join(c for c in wanted if c in _record_columns())
    if before is None:
        row = conn.execute(
            f"SELECT {selected} FROM node_results"
            " WHERE node_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (node_id,),
        ).fetchone()
    else:
        row = conn.execute(
            f"SELECT {selected} FROM node_results"
            " WHERE node_id=? AND created_at < ? ORDER BY created_at DESC LIMIT 1",
            (node_id, before),
        ).fetchone()
    if row is None:
        return None
    result = read_node_record(row)
    result["_dep_timestamps"] = json.loads(row["dep_timestamps"]) if row["dep_timestamps"] else {}
    result["_persisted_at"] = row["created_at"]
    result["_code_version"] = row["code_version"]
    return result


# lucidlint: ignore record-shape the tree IS the stored wire shape (serialization boundary)
def latest_node_provenance(node_id: str) -> dict[str, Any] | None:
    """The node's recorded provenance tree, or None.

    The ONLY read that inflates the compressed provenance blob — every other
    reader skips the column entirely, which is the point of the split: the
    trees are the bulk of the table and only the provenance endpoint wants
    them.
    """
    if not _table_exists("node_results"):
        init_db()
        return None
    _ensure_code_version_column()
    _ensure_split_columns()
    conn = _get_db()
    columns = _record_columns()
    wanted = [c for c in ("provenance_z", "result_json") if c in columns]
    if not wanted:
        return None
    row = conn.execute(
        f"SELECT {', '.join(wanted)} FROM node_results"
        " WHERE node_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (node_id,),
    ).fetchone()
    if row is None:
        return None
    keys = row.keys()
    if "provenance_z" in keys and row["provenance_z"] is not None:
        return cast(dict[str, Any], json.loads(decompress_result(row["provenance_z"])))
    if "result_json" in keys and row["result_json"]:
        # A pre-split row kept its tree inside the legacy blob.
        legacy = _read_legacy_record(row["result_json"]).get(PROVENANCE_KEY)
        return legacy if isinstance(legacy, dict) else None
    return None


# lucidlint: ignore record-shape wire-format dict — serialization boundary (same stored node payload as
# latest_node_result, read strictly-before a timestamp) (coding-standards.md)
def node_result_before(node_id: str, before: str) -> dict[str, Any] | None:
    """Return the most recent to_json() dict for a node STRICTLY BEFORE
    the ISO-8601 timestamp *before*, or None.

    node_results is append-only history ("each call appends a new row"),
    so this is a reference into the DAG's own past — e.g. the what-if
    restore reads the persons attempt from before the scenario started.
    Reads never block on writes — see latest_node_result.
    """
    return _fetch_latest_row(node_id, before)


def _table_exists(name: str) -> bool:
    conn = _get_db()
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def property_created_at(rid: str) -> str | None:
    """Return the ISO-8601 timestamp of the earliest node_result for a property.

    This is when the property was first added (the first UserInputNode push).
    Returns None if no node_results exist for this RID.
    """
    if not _table_exists("node_results"):
        return None
    conn = _get_db()
    row = conn.execute(
        # GLOB, not LIKE: LIKE is case-insensitive, so SQLite cannot use
        # idx_nr_node's node_id prefix for the pattern and scans the whole
        # 1M+ row table per property — measured 127ms x 46 properties on
        # every /api/properties/all load. GLOB is case-sensitive and its
        # prefix optimization hits the index: 2.7ms.
        "SELECT MIN(created_at) FROM node_results WHERE node_id GLOB ?",
        (f"{rid}/*",),
    ).fetchone()
    return row[0] if row and row[0] else None


def delete_node_results_for_rid(rid: str) -> None:
    """Remove every persisted row for a property (user-removed)."""
    # rid is user-supplied path input — escape LIKE wildcards so a
    # '%'/'_' cannot turn this into a delete of unrelated properties'
    # rows (PR #68 review, High).
    escaped = rid.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    conn = _get_db()
    conn.execute(
        "DELETE FROM node_results WHERE node_id LIKE ? ESCAPE '\\'",
        (f"{escaped}/%",),
    )
    conn.commit()


#: Node-id namespaces that are not properties.  The global settings nodes
#: live under ``settings/<name>``; the slash-less sources (``persons``,
#: ``financial``, …) never reach this query.
NON_PROPERTY_NAMESPACES = frozenset({"settings"})


def property_rids() -> list[str]:
    """Every node-id prefix that could be a property RID.

    The global settings namespace is excluded; nothing else is.  Filtering
    for numeric prefixes here made the startup loader's pollution guard
    unreachable — a row under a test-data id was silently ignored instead of
    failing loudly, which is the opposite of what that guard exists for.
    """
    if not _table_exists("node_results"):
        return []
    conn = _get_db()
    rows = conn.execute(
        "SELECT DISTINCT SUBSTR(node_id, 1, INSTR(node_id, '/') - 1) AS rid FROM node_results WHERE node_id LIKE '%/%'"
    ).fetchall()
    return sorted(rid for rid in {r[0] for r in rows} if rid and rid not in NON_PROPERTY_NAMESPACES)
