from __future__ import annotations

import ast
import asyncio
import hashlib
import inspect
import inspect as _inspect
import logging
import traceback
from abc import abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from inspect import iscoroutine
from types import FunctionType
from typing import Any, Generic, NamedTuple, TypeVar, cast, override

from dag.attempt import Attempt, AttemptError, Formula, Provenance, SourceType, classify_exception, project_value
from dag.eval_context import staged_attempt
from dag.expression import Expression
from dag.node import Node
from dag.scheduler import get_scheduler
from dag.signals import Connection, Slot

logger = logging.getLogger(__name__)

T = TypeVar("T")


# lucidlint: ignore global-state bounded module cache/state — single writer, deliberate
_CODE_VERSION_CACHE: dict[type, str] = {}
_CODE_VERSION_EPOCH: int = 0


def _normalize_compute_source(source: str) -> str:
    """Behavior-only normalization of a compute function's source.

    Comments and whitespace carry no behavior — an unparse round-trip
    yields identical text for comment-only or reformatted edits, so a
    non-behavioral commit cannot invalidate every persisted fingerprint
    (the recompute-storm the review flagged).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    return ast.unparse(tree)
class _HelperRef(NamedTuple):
    """A (name, module) function reference queued for source resolution."""

    name: str
    module: object


@dataclass(frozen=True)
class _CallTargets:
    """Call sites in one function body, grouped by how each resolves."""

    called: list[str]
    self_methods: list[str]
    qualified: list[tuple[str, str]]


@dataclass(frozen=True)
class _HelperSources:
    """Normalized helper sources plus their own unresolved references."""

    parts: list[str]
    queue: list[_HelperRef]


def _bound_names(tree: ast.AST) -> set[str]:
    """Names bound locally in ``tree`` — params, stores, imports.

    Function NAMES CALLED in the body only resolve to module-level defs
    when they aren't shadowed by a local binding (a parameter,
    assignment, or import) — a collision with a local must not pull an
    unrelated same-module helper into the fingerprint.
    """
    bound: set[str] = set()
    # lucidlint: ignore loop-pipeline group-by/control-flow — comprehension cannot express
    for node in ast.walk(tree):
        if isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, ast.Import):
            bound.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            bound.update(alias.asname or alias.name for alias in node.names)
    return bound


def _call_targets(tree: ast.AST, bound: set[str]) -> _CallTargets:
    """Call targets in ``tree``, split by how each resolves:

    - plain names that aren't locally bound (same-module function calls),
    - ``self.attr`` method calls,
    - ``module.func`` qualified calls.

    Only actual Call targets count — a bare name used as a value or
    attribute base must not pull in an unrelated helper.
    """
    called: list[str] = []
    self_methods: list[str] = []
    qualified: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            if node.func.id not in bound:
                called.append(node.func.id)
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            if node.func.value.id == "self":
                self_methods.append(node.func.attr)
            else:
                # module-qualified call: address_utils.normalise(...) —
                # the base name resolves to an imported module.
                qualified.append((node.func.value.id, node.func.attr))
    return _CallTargets(called, self_methods, qualified)


def _enclosing_class(module, func: FunctionType) -> type | None:
    """The class owning ``func``, resolved in its module via qualname."""
    if "." not in func.__qualname__:
        return None
    cls_name = func.__qualname__.rsplit(".", 1)[0]
    for _name, obj in vars(module).items():
        if _inspect.isclass(obj) and obj.__qualname__ == cls_name:
            return obj
    return None


def _self_helper_sources(node_cls: type, self_methods: list[str]) -> _HelperSources:
    """Sources of the class methods called via ``self``, plus their own
    plain-name calls queued for resolution in the method's module.

    ``getattr`` resolves through the MRO so a helper inherited from a
    base class in another module is found.
    """
    parts: list[str] = []
    queue: list[_HelperRef] = []
    for mname in self_methods:
        method = getattr(node_cls, mname, None)
        if method is None:
            continue
        try:
            msrc = _inspect.getsource(method)
        except (OSError, TypeError):
            continue
        parts.append(_normalize_compute_source(msrc))
        try:
            mtree = ast.parse(msrc)
        except SyntaxError:
            continue
        m_module = _inspect.getmodule(method)
        queue.extend(
            _HelperRef(n.func.id, m_module)
            for n in ast.walk(mtree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        )
    return _HelperSources(parts, queue)


    # lucidlint: ignore record-shape wire-format (module, name) resolution pairs — consumed once by this BFS
def _module_call_queue(module, called: list[str], qualified: list[tuple[str, str]]) -> list[_HelperRef]:
    """Initial resolution queue: plain names in the function's own
    module, module-qualified names through their import."""
    queue: list[_HelperRef] = [_HelperRef(name, module) for name in called]
    for mod_name, fn_name in qualified:
        mod_obj = vars(module).get(mod_name)
        if mod_obj is not None and _inspect.ismodule(mod_obj):
            queue.append(_HelperRef(fn_name, mod_obj))
    return queue


def _resolve_helper_sources(queue: list[_HelperRef]) -> list[str]:
    """BFS-resolve queued references to their normalized sources,
    transitively (bounded, cycle-safe).  Cross-module imports resolve in
    their OWN module — a change to a shared helper (e.g.
    address_utils.normalise) must invalidate the fingerprint even though
    it lives elsewhere."""
    seen: set[tuple[str, str]] = set()
    parts: list[str] = []
    while queue:
        name, mod = queue.pop()
        key = (name, getattr(mod, "__name__", ""))
        if key in seen:
            continue
        seen.add(key)
        obj = vars(mod).get(name) if mod is not None else None
        if obj is None or not _inspect.isfunction(obj):
            continue
        try:
            hsrc = _inspect.getsource(cast(FunctionType, obj))
        except (OSError, TypeError):
            continue
        parts.append(_normalize_compute_source(hsrc))
        # recurse into the helper's own references, resolved in ITS module
        try:
            htree = ast.parse(hsrc)
        except SyntaxError:
            continue
        h_module = _inspect.getmodule(obj)
        queue.extend(
            _HelperRef(n.func.id, h_module)
            for n in ast.walk(htree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        )
    return parts


def _referenced_helper_sources(func: FunctionType) -> list[str]:
    """AST-normalized sources of the same-module helpers ``func`` calls.

    The fingerprint must cover the compute's helpers too — a behavior
    change in ``_infeasible_commute``/``_lookup_yearly_cost``-style
    module functions leaves compute()'s text unchanged, so persisted
    results would stay "fresh" forever.  Walks name references that
    resolve to module-level defs in the function's own module,
    transitively, cycle-safe.  Calls behind the services boundary
    (svc.xxx_service.lookup) are not statically resolvable and are out
    of scope — the node's compute text still covers its own logic.
    """
    module = _inspect.getmodule(func)
    if module is None:
        return []
    try:
        func_src = _inspect.getsource(func)
    except (OSError, TypeError):
        return []
    try:
        tree = ast.parse(func_src)
    except SyntaxError:
        return []
    bound = _bound_names(tree)
    targets = _call_targets(tree, bound)
    parts: list[str] = []
    queue = _module_call_queue(module, targets.called, targets.qualified)
    node_cls = _enclosing_class(module, func)
    if node_cls is not None:
        sources = _self_helper_sources(node_cls, targets.self_methods)
        parts.extend(sources.parts)
        queue.extend(sources.queue)
    parts.extend(_resolve_helper_sources(queue))
    return sorted(parts)


def _compute_code_version(node: DerivedNode) -> str:
    """Fingerprint of the compute code — changes when the computation
    changes, so persisted results computed by older code can be detected.

    The hash covers the class identity plus the AST-normalized compute
    function source (comments/whitespace do not change the fingerprint).
    Cached per class (compute is a bound method — same code for every
    instance).  Returns "" when the source can't be introspected (e.g.
    dynamically generated functions) — callers treat that as "cannot
    fingerprint", never as a mismatch.
    """
    cls = type(node)
    cached = _CODE_VERSION_CACHE.get(cls)
    if cached is not None:
        return cached
    func = node.compute
    if inspect.ismethod(func):
        func = func.__func__
    try:
        raw = inspect.getsource(func)
    except (OSError, TypeError):
        _CODE_VERSION_CACHE[cls] = ""
        return ""
    normalized = _normalize_compute_source(raw)
    helpers = _referenced_helper_sources(func)
    # Nodes also decide behavior in __init__ (mode alternatives, dep
    # gating) and _get_active_deps — a change to those leaves compute()
    # identical, so include their sources too (the review's gap).
    structure_parts = []
    for member in ("__init__", "_get_active_deps"):
        try:
            structure_parts.append(_normalize_compute_source(_inspect.getsource(getattr(cls, member))))
        except (OSError, TypeError):
            continue
    digest = hashlib.sha256(
        (
            f"{cls.__module__}.{cls.__qualname__}:{normalized}:{'|'.join(helpers)}:"
            f"{'|'.join(structure_parts)}"
        ).encode()
    ).hexdigest()[:16]
    _CODE_VERSION_CACHE[cls] = digest
    # Bump the epoch whenever a NEW fingerprint is computed — the epoch
    # is the cheap 'did any code change since my last scan' signal.
    # lucidlint: ignore global-state bounded module cache/state — single writer, deliberate
    global _CODE_VERSION_EPOCH
    _CODE_VERSION_EPOCH += 1
    return digest


def _check_compute_arity(node: DerivedNode, dep_attempts: list[Attempt]) -> None:
    """Fail loudly when a positional compute can't accept the dep attempts.

    A silent mismatch surfaces later as a confusing TypeError or — worse —
    a value bound to the wrong parameter.  Named-deps nodes are exempt
    (names bind), and varargs computes accept anything.
    """
    sig = inspect.signature(node.compute)
    params = list(sig.parameters.values())
    if any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params):
        return
    n = len(dep_attempts)
    positional = [
        p
        for p in params
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    min_args = sum(1 for p in positional if p.default is inspect.Parameter.empty)
    # Upper bound = positionally-fillable params ONLY — keyword-only and
    # **kwargs can't absorb positional deps, so counting them inflated
    # the bound and let a bare TypeError escape the guard.
    if not (min_args <= n <= len(positional)):
        raise ValueError(
            f"{node._id}: compute() takes {min_args}–{len(positional)} positional "
            f"argument(s) but {n} dep attempt(s) were passed — the deps and "
            f"the compute signature have drifted. Declare dep_names or fix the deps."
        )


class DerivedNode(Node[T], Generic[T]):
    """A node whose value is computed from other nodes."""

    # ── Expression system ────────────────────────────────
    # Subclasses set ``expression`` (or override it as a ``@property``)
    # to an ``Expression`` tree. If set, ``build_provenance`` uses it
    # for formula generation.

    _expression: Expression | None = None

    @property
    def expression(self) -> Expression | None:
        return self._expression

    def _build_formula_from_expression(self) -> Formula | None:
        """Build a Formula from the expression tree, if one is set."""
        expr = self.expression
        if expr is None:
            return None
        try:
            return expr.to_formula()
        # lucidlint: ignore broad-except deliberate degrade — formula rendering failure falls back to None
        except Exception:
            return None

    def __init__(
        self,
        node_id: str,
        value_type: type[T],
        deps: tuple[Node, ...],
        source_url: str = "",
        dep_names: tuple[str, ...] | None = None,
    ) -> None:
        super().__init__(node_id, value_type, source_url)
        self._deps = deps
        self._dep_names = dep_names
        if dep_names is not None and len(dep_names) != len(deps):
            raise ValueError(f"{self._id}: dep_names ({len(dep_names)}) must match deps ({len(deps)})")
        self._attempt: Attempt[T] = Attempt.pending()
        self._connections: list[Connection] = []
        self._slots: list[Slot] = []

        loaded = self._load_attempt_from_db()
        if loaded is not None:
            self._attempt = loaded
        for i, dep in enumerate(deps):
            if dep is None:
                raise ValueError(
                    f"{self._id}: dependency at index {i} is None — "
                    f"DAG nodes must not have None dependencies. "
                    f"Check the caller's deps list."
                )
        for dep in deps:
            slot = Slot(self._on_dep_changed)
            self._slots.append(slot)
            conn = dep.changed.connect(slot)
            self._connections.append(conn)

        get_scheduler().register(self)

    # ── Compute dispatch ───────────────────────────────
    # compute() is called with the active deps' attempts.  Nodes whose
    # deps are CONDITIONAL (built dynamically, or _get_active_deps
    # gating a subset) declare ``dep_names`` so attempts bind by NAME —
    # a dropped middle dep can never shift later arguments into the
    # wrong parameter (the historical group-node misalignment).  Other
    # nodes stay positional, guarded by an arity check that fails
    # loudly instead of letting a mismatch surface as a confusing
    def _call_compute(self, dep_attempts: list[Attempt], active_deps: tuple[Node, ...]) -> Any:
        # Returns either the Attempt or a coroutine resolving to one —
        # the caller awaits via iscoroutine().  Typed as Any because the
        # two shapes defeat a static union (attribute access on the
        # coroutine branch is checked at the await site).
        if self._dep_names is not None:
            # Bind by dep identity against the static deps, so a
            # non-trailing subset of active deps still reaches the
            # right parameter.  Omitted names rely on compute defaults —
            # and a REQUIRED param with no active dep fails loudly (the
            # named branch must not be quieter than the positional
            # arity guard).
            kwargs: dict[str, Attempt] = {}
            for name, dep in zip(self._dep_names, self._deps, strict=True):
                if dep in active_deps:
                    kwargs[name] = dep_attempts[active_deps.index(dep)]
            missing = [
                p.name
                for p in inspect.signature(self.compute).parameters.values()
                if p.default is inspect.Parameter.empty
                and p.kind
                in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
                and p.name not in kwargs
            ]
            if missing:
                raise ValueError(
                    f"{self._id}: compute() requires {missing} but those deps are "
                    f"not active — give the parameters defaults or keep the deps active"
                )
            result = self.compute(**kwargs)
        else:
            _check_compute_arity(self, dep_attempts)
            result = self.compute(*dep_attempts)
        return result if not iscoroutine(result) else result

    # ── Code-version stamp ─────────────────────────────
    # Every persisted result records a fingerprint of the compute code
    # that produced it.  A persisted row whose fingerprint differs from
    # the current compute is stale-in-code: recompute even though every
    # dep timestamp says "fresh" (the gap that left stale TypeError
    # results sitting on live properties after a code change).

    def _current_code_version(self) -> str:
        return _compute_code_version(self)

    def code_is_stale(self) -> bool:
        """True when the persisted result was produced by different code.

        ``None`` means the node was never persisted (a fresh in-memory
        attempt) — never stale.  ``""`` means a persisted row written
        before the version stamp existed (old code) — stale once.
        """
        persisted = getattr(self, "_persisted_code_version", None)
        current = self._current_code_version()
        if current:
            return persisted is not None and persisted != current
        # Can't fingerprint this compute — but a persisted row WITH a
        # fingerprint came from different code: recompute once (the
        # stored version then matches "" and the check stays stable).
        return persisted is not None and persisted != ""

    def disconnect(self) -> None:
        """Disconnect all signal connections and unregister from the scheduler."""
        for conn in self._connections:
            conn.disconnect()
        self._connections.clear()
        get_scheduler().unregister(self)

    def _get_active_deps(self) -> tuple[Node, ...]:
        return self._deps

    @override
    def latest_attempt(self) -> Attempt:
        # During a scenario evaluation (dag.evaluate), the staged
        # hypothetical attempt shadows the real one — compute bodies
        # reading deps through this path see the what-if transparently.
        staged = staged_attempt(self._id)
        return staged if staged is not None else self._attempt

    def _on_dep_changed(self) -> None:
        self._retry_count = 0
        if not self._is_stale():
            return
        get_scheduler().schedule(self)

    def _is_stale(self) -> bool:
        # Staleness is a NORMAL condition (any dep change re-schedules the
        # downstream node) — the old STALE1/2/3/CODE warnings logged at
        # WARNING on every pass and flooded the log during the code-version
        # migration sweep.  The checks are the logic; the noise is gone.
        if self.code_is_stale():
            return True
        if self._retry_at is not None:
            return True
        if self._attempt.pending:
            return True
        for dep in self._get_active_deps():
            if (
                dep._persisted_at is not None
                and self._computed_at is not None
                and dep._persisted_at > self._computed_at
            ):
                return True
            if (
                isinstance(dep, DerivedNode)
                and dep._computed_at is not None
                and self._computed_at is not None
                and dep._computed_at > self._computed_at
            ):
                return True
            if self._loaded_dep_timestamps:
                stored = self._loaded_dep_timestamps.get(dep._id, "")
                if stored and not dep._db_created_at:
                    continue
                if stored and dep._db_created_at != stored:
                    return True
        return False

    @override
    async def attempt(self) -> Attempt[T]:
        return self._attempt

    @staticmethod
    def _is_transient_error(exc: Exception) -> bool:
        """Identify retryable transient errors.

        Delegates to :func:`dag.attempt.classify_exception` — the single
        source of truth also used by AttemptError — so the DAG retry
        decision matches what gets recorded on the Attempt. Handles
        TimeoutError, HttpError (``.status``), httpx errors
        (``.response.status_code``), and any status-aware exception.
        """
        return classify_exception(exc).retryable

    def schedule_retry(self, delay: timedelta) -> bool:
        """Schedule a DAG-level retry at now + delay.

        Returns True if the retry was scheduled, False if max retries exceeded.
        When False, the caller should return ``Attempt.impossible`` instead of
        ``Attempt.pending()`` so the node doesn't stay pending forever.
        """
        if self._retry_count >= self._max_retries:
            return False
        self._retry_at = datetime.now(UTC) + delay
        get_scheduler().schedule_at(self, self._retry_at)
        return True

    def _retry_delay_from(self, exc: Exception, base_delay: timedelta = timedelta(seconds=10)) -> timedelta:
        """Extract retry delay from an exception, or use exponential backoff."""
        retry_after = getattr(exc, "retry_after", None)
        if retry_after is not None:
            return timedelta(seconds=min(retry_after, 300))
        delay_sec = base_delay.total_seconds() * (2**self._retry_count)
        if self._retry_count < self._max_retries:
            self._retry_count += 1
        return timedelta(seconds=min(delay_sec, 300))

# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    async def _error_result_dict(self, status: str, exc: Exception) -> dict:
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
        return {
            "status": status,
            "value": None,
            "error": f"{self._id}: {exc}",
            "error_detail": AttemptError.from_exception(str(exc), exc, source=self._id).to_dict(),
            "provenance": await self._build_provenance_dict(),
        }

# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    async def _safe_result_dict(self, status: str) -> dict:
        """Serialize for persistence, degrading to an error result on failure.

        ``to_json`` can raise when a value lacks a provenance projection
        (e.g. during a failed recompute). The degraded error result still
        records the failure so the persisted row explains itself.
        """
        try:
            return await self.to_json()
        # lucidlint: ignore broad-except to_json failure degrades to an error-result dict so the attempt still persists
        except Exception as e:
            logger.debug(
                "%s: to_json failed; persisting an error result for status %r: %s",
                self._id,
                status,
                e,
            )
            return await self._error_result_dict(status, e)

    async def _compute_attempt(self, dep_attempts: list[Attempt], active_deps: tuple[Node, ...]) -> Attempt:
        """Run compute, converting failures into pending/impossible Attempts.

        Transient errors schedule a retry (pending); other failures become
        impossible with the exception recorded — mirroring the DAG error
        contract. Never raises: the outcome always comes back as an Attempt.
        """
        try:
            result = self._call_compute(dep_attempts, active_deps)
            if iscoroutine(result):
                result = await result
                # Yield to event loop so HTTP requests aren't starved during
                # burst refresh (many nodes queued at startup).
                await asyncio.sleep(0)
            return result
        # lucidlint: ignore broad-except boundary — compute failures convert to pending/impossible Attempts, never raise
        except Exception as e:
            if self._is_transient_error(e):
                if not self.schedule_retry(self._retry_delay_from(e)):
                    return Attempt.impossible(
                        f"{self._id}: retry exhausted ({e})",
                        error_info=AttemptError.from_exception(str(e), e, source=self._id),
                    )
                return Attempt.pending()
            impossible_deps = [a for a in dep_attempts if a.impossible]
            if impossible_deps:
                errors = "; ".join(a.error or "unknown" for a in impossible_deps)
                return Attempt.impossible(
                    f"{self._id}: dep failed ({errors})",
                    error_info=AttemptError.from_exception(str(e), e, source=self._id),
                )
            return Attempt.impossible(
                f"{self._id}: {e}",
                error_info=AttemptError.from_exception(str(e), e, source=self._id),
            )

    async def refresh(self, force: bool = False) -> None:
        """Recompute and persist this node.

        Skips nodes whose inputs haven't changed since they were last
        computed.  Code changes are caught by the code-version stamp
        (a persisted row computed by different code is stale).
        ``force=True`` bypasses the staleness check entirely — for
        explicit full recomputes (admin regenerate).
        """
        if not force and not self._is_stale():
            return
        active_deps = self._get_active_deps()
        for i, dep in enumerate(active_deps):
            if dep is None:
                raise ValueError(
                    f"{self._id}._get_active_deps() returned None at index {i}. "
                    f"Active deps: {[d._id if d else None for d in active_deps]}"
                )
        dep_attempts = [await dep.attempt() for dep in active_deps]
        # Propagate impossible before checking pending — if a dep is
        impossible_deps = [a for a in dep_attempts if a.impossible]
        if impossible_deps:
            errors = "; ".join(a.error or "unknown" for a in impossible_deps)
            message = f"{self._id}: dep failed ({errors})"
            causes = tuple(a.error_info for a in impossible_deps if a.error_info is not None)
            if causes:
                result = Attempt.impossible(
                    message,
                    error_info=AttemptError(
                        code="dep_failed",
                        message=message,
                        source=self._id,
                        causes=causes,
                    ),
                )
            else:
                result = Attempt.impossible(message)
            self._attempt = result
            self._computed_at = datetime.now(UTC)
            self._persisted_code_version = self._current_code_version()
            # _db_created_at may be None for deps never persisted (e.g. a
            # freshly-created node).  Storing None means the next staleness
            # check skips this dep — the _computed_at comparison still works.
            dep_timestamps = {dep._id: dep._db_created_at for dep in active_deps}
            self._retry_at = None
            self._retry_count = 0
            result_dict = await self._safe_result_dict("impossible")
            self._persist(result_dict, dep_timestamps, code_version=self._current_code_version())
            self.changed.emit()
            get_scheduler().after_refresh(self)
            return
        if any(a.pending for a in dep_attempts):
            return
        result = await self._compute_attempt(dep_attempts, active_deps)

        # A service may catch a transient error and RETURN an impossible
        # attempt whose error_info.retryable is set (rather than raising).
        # The retry decision must not depend on which style the service
        # used — retry either way, using the exception it recorded.
        if result.impossible and not result.pending:
            info = result.error_info
            if (
                info is not None
                and info.retryable
                and isinstance(info.exc, Exception)
                and self.schedule_retry(self._retry_delay_from(info.exc))
            ):
                result = Attempt.pending()
        # requests before we do sync persist work (json.dumps + SQLite).
        await asyncio.sleep(0)

        self._attempt = result
        self._computed_at = datetime.now(UTC)
        self._persisted_code_version = self._current_code_version()

        # _db_created_at may be None for deps never persisted (e.g. a
        # freshly-created node).  Storing None means the next staleness
        # check skips this dep — the _computed_at comparison still works.
        dep_timestamps = {dep._id: dep._db_created_at for dep in active_deps}

        if result.pending:
            result_dict = await self._safe_result_dict("pending")
            self._persist(result_dict, dep_timestamps, code_version=self._current_code_version())
            return

        self._retry_at = None
        self._retry_count = 0 if result.succeeded else self._retry_count

        result_dict = await self._safe_result_dict("impossible")
        self._persist(result_dict, dep_timestamps, code_version=self._current_code_version())
        self.changed.emit()
        get_scheduler().after_refresh(self)

# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    async def _build_provenance_dict(self) -> dict:
        """Build provenance dict for persistence, with a fallback if build_provenance() fails."""
        try:
            prov = await self.build_provenance()
            return prov.to_dict()
        # lucidlint: ignore broad-except deliberate degrade — provenance build failure falls back to an empty label
        except Exception:
            return {"label": ""}

    @override
    async def build_provenance(self) -> Provenance:
        sources: dict[str, Provenance] = {}
        for dep in self._get_active_deps():
            sources[dep._id] = await self._dep_provenance(dep)

        # Use expression system for formula if available
        formula = self.provenance_formula
        if formula is None:
            formula = self._build_formula_from_expression()

        status = "impossible" if self._attempt.impossible else ("pending" if self._attempt.pending else "")
        error_info = self._attempt.error_info
        # The provenance error/description feed the UI — use the friendly
        # user_message, never the internal node-id/dep chain.
        user_error = error_info.display_message if error_info is not None else self._attempt.error
        # A succeeded-infeasible commute (TfL 404 "no route", missing
        # destination, no car) carries its reason on the value — surface
        # it as the description so the provenance explains WHY there is no
        # route.  Duck-typed: only values with both attributes contribute.
        val = self._attempt.value_or_none()
        no_route_reason = ""
        if self._attempt.succeeded and val is not None and getattr(val, "infeasible", False):
            no_route_reason = getattr(val, "no_route_reason", "") or ""
        description = user_error if self._attempt.impossible else (no_route_reason or None)
        return Provenance(
            label=self.display_name,
            description=description,
            value=project_value(self._attempt.value),
            url=self._source_url,
            source_type=self.provenance_source_type,
            freshness=self._attempt.created_at,
            formula=formula,
            status=status,
            error=user_error if self._attempt.impossible else "",
            sources=sources,
        )

    async def _dep_provenance(self, dep: Node) -> Provenance:
        """Provenance for one dependency, degrading on failure.

        One failing dep must not break the whole provenance tree — the
        error is captured in the placeholder's description instead.
        """
        try:
            return await dep.build_provenance()
        # lucidlint: ignore broad-except deliberate degrade — one failing dep degrades to a placeholder provenance
        except Exception as e:
            logger.debug(
                "%s: build_provenance failed for dep %s; degrading to a placeholder: %s",
                self._id,
                dep._id,
                e,
            )
            return Provenance(
                label=getattr(dep, "display_name", dep._id),
                description=f"build_provenance failed: {e}\n{traceback.format_exc()}",
            )

    provenance_source_type: SourceType = SourceType.CALC
    """Subclass declares its data source category.
    Default is CALC — override in subclasses that source from
    APIs, geocoding, config, or user input."""

    provenance_formula: Formula | None = None
    """Override to return a Formula for computed values.
    Default is None — no formula."""

    @override
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    async def to_json(self) -> dict:
        result = await super().to_json()
        if self._retry_at is not None:
            result["retry_at"] = self._retry_at.isoformat()
            result["retry_count"] = self._retry_count
        if not self._attempt.pending:
            result["stale"] = self._is_stale()
        return result

    @override
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    async def to_json_value(self) -> dict:
        result = await super().to_json_value()
        if self._retry_at is not None:
            result["retry_at"] = self._retry_at.isoformat()
            result["retry_count"] = self._retry_count
        if not self._attempt.pending:
            result["stale"] = self._is_stale()
        return result

    @staticmethod
    def _assert_deps_succeeded(**deps: Attempt) -> None:
        """Assert all named dependencies are succeeded.

        Auto-propagation in _refresh() catches impossible/pending deps
        before compute() is ever called.  This assertion is a safety net
        to fail fast if that contract is violated.
        """
        failed = {name: att.status for name, att in deps.items() if att is not None and not att.succeeded}
        if failed:
            raise AssertionError(
                f"Dependencies not succeeded: {failed}. Auto-propagation should have caught these before compute()."
            )

    @staticmethod
    @abstractmethod
    def compute(*dep_attempts: Attempt) -> Attempt[T]: ...
