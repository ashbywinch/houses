"""Backfill enrichment — batch re-enrich existing properties from the sheet."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from houses.config import settings
from houses.enrichment_runner import (
    asdict_serializable,
    extract_postcode,
    header_to_enrichment_field,
    is_outcode,
    run_backfill_enrichment,
)
from houses.property import EnrichedProperty
from houses.sheets import Tab, col_letter, row_values, write_enriched_row

logger = logging.getLogger(__name__)


def _json_line(data: dict) -> str:
    """Pretty-print JSON for streaming output lines."""
    return json.dumps(data) + "\n"


async def batch_stream(
    gclient: Any,
    no_write: bool,
    fields: list[str] | None,
    rids: str | None,
    force: bool = False,
) -> AsyncGenerator[str, None]:
    """Backfill enrichment: read View tab, enrich missing fields, yield NDJSON."""
    try:
        sh = gclient.open_by_key(settings.sheet_id)
        view_ws = sh.worksheet("Properties View")
        data_ws = sh.worksheet("Properties Data")
    except Exception as exc:
        logger.error("Failed to open sheet: %s", exc)
        yield json.dumps({"status": "error", "error": str(exc)}) + "\n"
        return

    view_data = view_ws.get_all_values()
    if len(view_data) < 2:
        yield json.dumps({"status": "ok", "message": "View tab is empty", "results": []}) + "\n"
        return

    view_headers = view_data[0]
    vh = {h.strip().lower(): i for i, h in enumerate(view_headers)}
    url_col = vh.get("rightmove link")
    id_col = vh.get("rightmove id")
    addr_col = vh.get("listing address")

    data_all = data_ws.get_all_values()
    data_headers = data_all[0] if data_all else []

    try:
        data_rid_idx = data_headers.index("Rightmove ID")
    except ValueError:
        data_rid_idx = -1

    user_cols = frozenset({"Actual Latitude", "Actual Longitude"})
    enriched_col_indices: dict[str, int] = {}
    for i, h in enumerate(data_headers):
        if h not in user_cols:
            enriched_col_indices[h] = i

    user_fields: set[str] | None = None
    if fields:
        user_fields = set()
        for f in fields:
            for part in f.split(","):
                p = part.strip()
                if p:
                    user_fields.add(p)
    target_rids: set[str] = {r.strip() for r in rids.split(",")} if rids else set()
    processed_rids: set[str] = set()
    total = len(view_data) - 1
    summary: dict[str, int] = {"updated": 0, "skipped": 0, "created": 0, "errors": 0}

    yield _json_line({"type": "start", "total": total, "no_write": no_write, "force": force, "rids": rids})

    for row_idx, view_row in enumerate(view_data[1:], 2):
        url_raw = view_row[url_col].strip() if url_col is not None and url_col < len(view_row) else ""
        raw_id = view_row[id_col].strip() if id_col is not None and id_col < len(view_row) else ""
        if not raw_id:
            summary["skipped"] += 1
            yield _json_line({"type": "row", "row": row_idx, "rid": None, "status": "skipped", "reason": "no RID"})
            continue
        rid = raw_id
        if target_rids and rid not in target_rids:
            summary["skipped"] += 1
            yield _json_line({"type": "row", "row": row_idx, "rid": rid, "status": "skipped", "reason": "not in rids"})
            continue
        if rid in processed_rids:
            summary["skipped"] += 1
            yield _json_line({"type": "row", "row": row_idx, "rid": rid, "status": "skipped", "reason": "duplicate"})
            continue
        processed_rids.add(rid)

        url = url_raw if url_raw.startswith("http") else f"https://www.rightmove.co.uk/properties/{rid}"
        address = view_row[addr_col].strip() if addr_col is not None and addr_col < len(view_row) else ""
        data_row = data_all[row_idx - 1] if len(data_all) > row_idx - 1 else [""] * len(data_headers)
        existing_rid = data_row[data_rid_idx].strip() if data_rid_idx >= 0 and len(data_row) > data_rid_idx else ""
        data_row_num = row_idx if (existing_rid == rid or not existing_rid) else 0

        if data_row_num == 0:
            new_pc = extract_postcode(address) or ""
            enriched = await run_backfill_enrichment(
                url=url,
                address=address,
                postcode=new_pc,
                lookup=None,
                bedrooms=None,
                price=None,
                enabled=None,
            )
            flat = row_values(enriched)
            if no_write:
                yield _json_line(
                    {
                        "type": "row",
                        "row": row_idx,
                        "rid": rid,
                        "status": "would_create",
                        "enriched": asdict_serializable(enriched),
                        "flat": flat,
                    }
                )
            else:
                row_url_result = await write_enriched_row(enriched)
                summary["created"] += 1
                yield _json_line(
                    {
                        "type": "row",
                        "row": row_idx,
                        "rid": rid,
                        "status": "created",
                        "row_url": row_url_result,
                    }
                )
            continue

        # Decide which columns to consider:
        # - ``fields`` restricts to specific enrichment fields (column groups)
        # - ``force`` controls whether we overwrite existing values or only
        #   fill blank cells
        if user_fields:
            consider_headers = [h for h in data_headers if (ef := header_to_enrichment_field(h)) and ef in user_fields]
        else:
            consider_headers = list(enriched_col_indices.keys())

        if force:
            empty_headers = [h for h in consider_headers if h in enriched_col_indices]
        else:
            empty_headers = [
                h
                for h in consider_headers
                if (ci := enriched_col_indices.get(h)) is not None and (ci >= len(data_row) or not data_row[ci].strip())
            ]

        if not empty_headers:
            summary["skipped"] += 1
            yield _json_line(
                {"type": "row", "row": row_idx, "rid": rid, "status": "skipped", "reason": "already fully enriched"},
            )
            continue

        needed = set()
        for h in empty_headers:
            ef = header_to_enrichment_field(h)
            if ef:
                needed.add(ef)
        if user_fields is not None:
            needed &= user_fields

        addr = data_row[1].strip() if len(data_row) > 1 and data_row[1].strip() else address
        pc = data_row[2].strip() if len(data_row) > 2 and data_row[2].strip() else extract_postcode(addr)
        if "epc" in needed and (not pc or is_outcode(pc)):
            needed.discard("epc")
        if "council_tax" in needed and not addr:
            needed.discard("council_tax")

        if not needed:
            summary["skipped"] += 1
            yield _json_line({"type": "row", "row": row_idx, "rid": rid, "status": "skipped", "reason": "no fields"})
            continue

        yield _json_line({"type": "row", "row": row_idx, "rid": rid, "status": "enriching", "fields": sorted(needed)})
        enriched = await run_backfill_enrichment(
            url=url,
            address=addr,
            postcode=pc,
            lookup=None,
            bedrooms=None,
            price=None,
            enabled=needed if needed else None,
        )

        if no_write:
            flat = row_values(enriched)
            status = "would_create" if not existing_rid else "would_update"
            summary["skipped" if status == "would_update" else "created"] += 1
            yield _json_line(
                {
                    "type": "row",
                    "row": row_idx,
                    "rid": rid,
                    "status": status,
                    "fields": sorted(needed),
                    "enriched": asdict_serializable(enriched),
                    "flat": flat,
                }
            )
        else:
            _write_backfill_cells(
                sh,
                data_ws,
                data_row_num,
                data_headers,
                data_row,
                enriched,
                empty_headers,
                force=force,
                rid=rid,
            )
            yield _json_line({"type": "row", "row": row_idx, "rid": rid, "status": "updated", "fields": sorted(needed)})
            summary["updated"] += 1
        continue

    logger.info(
        "Batch done: %d updated, %d skipped, %d created — %s",
        summary["updated"],
        summary["skipped"],
        summary["created"],
        "force" if force else "blanks only",
    )
    yield _json_line({"type": "summary", **summary})


def _write_backfill_cells(
    sh: Any,
    ws: Any,
    row_num: int,
    headers: list[str],
    current_row: list[str],
    enriched: EnrichedProperty,
    allowed_headers: list[str],
    force: bool = False,
    rid: str = "",
) -> None:
    """Write enriched values to a Data tab row.

    Normally only writes to cells that are currently empty (never overwrites
    existing data). When ``force=True``, writes to all allowed_headers even
    if the cell already has data.
    """
    enriched_dict = row_values(enriched)
    allowed_set = set(allowed_headers)

    cells: list[dict[str, Any]] = []
    written: list[str] = []
    skipped: list[str] = []
    for name, val in enriched_dict.items():
        if not val or name not in allowed_set:
            continue
        try:
            col_idx = headers.index(name)
        except ValueError:
            continue
        if not force and col_idx < len(current_row) and current_row[col_idx].strip():
            skipped.append(name)
            continue
        cl = col_letter(col_idx)
        cells.append({"range": f"{cl}{row_num}", "values": [[val]]})
        written.append(name)

    if cells:
        Tab(ws).batch_update(cells)
        logger.info(
            "Wrote row %d (RID %s): %d cells [%s]",
            row_num,
            rid,
            len(cells),
            ", ".join(written),
        )
    if skipped:
        logger.info(
            "Skipped row %d (RID %s): %d cells already had data [%s]",
            row_num,
            rid,
            len(skipped),
            ", ".join(skipped),
        )
