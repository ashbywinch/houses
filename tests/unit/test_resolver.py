from __future__ import annotations

from datetime import datetime

import pytest

from houses.model import DerivedRow, NodeKind
from houses.model.persistence import (
    insert_source_value,
    insert_user_input,
    save_derived,
)
from houses.model.registry import NODES, node
from houses.model.resolver import _is_stale, check_staleness, resolve_property, topo_sort


@pytest.fixture(autouse=True)
def _sqlite_memory():
    import sqlite3

    import houses.model.persistence as per

    saved = per.get_db
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    per.get_db = lambda: conn
    per.init_db()
    yield
    per.get_db = saved


@pytest.fixture(autouse=True)
def _clear_nodes():
    saved = dict(NODES)
    NODES.clear()
    yield
    NODES.clear()
    NODES.update(saved)


# Register minimal test nodes
def _register_test_nodes():
    node(id="source_a", kind=NodeKind.source, provenance_template="test_a")(lambda: None)
    node(id="source_b", kind=NodeKind.source, provenance_template="test_b")(lambda: None)
    node(id="corrected_address", kind=NodeKind.user_input, user_table="user_corrected_address")(lambda: None)

    @node(id="derived_d", kind=NodeKind.derived, deps=["source_a", "corrected_address"])
    def derived_d(source_a: str | None, corrected_address: str | None) -> tuple[str, str]:
        return f"d({source_a},{corrected_address})", "computed"

    @node(id="derived_e", kind=NodeKind.derived, deps=["source_b", "derived_d"])
    def derived_e(source_b: str | None, derived_d: str | None) -> tuple[str, str]:
        return f"e({source_b},{derived_d})", "computed"


RID = "prop456"


class TestTopoSort:
    def test_simple_linear(self):
        _register_test_nodes()
        order = topo_sort(["source_a", "derived_d", "derived_e"])
        assert order.index("source_a") < order.index("derived_d")
        assert order.index("derived_d") < order.index("derived_e")

    def test_diamond(self):
        _register_test_nodes()
        NODES.clear()
        _register_test_nodes()
        node(id="source_x", kind=NodeKind.source)(lambda: None)
        node(id="source_y", kind=NodeKind.source)(lambda: None)

        @node(id="mid_x", kind=NodeKind.derived, deps=["source_x", "source_y"])
        def mid_x(source_x, source_y):
            return "x", "t"

        @node(id="mid_y", kind=NodeKind.derived, deps=["source_x", "source_y"])
        def mid_y(source_x, source_y):
            return "y", "t"

        @node(id="result", kind=NodeKind.derived, deps=["mid_x", "mid_y"])
        def result(mid_x, mid_y):
            return "r", "t"

        order = topo_sort(["source_x", "source_y", "mid_x", "mid_y", "result"])
        assert order.index("source_x") < order.index("mid_x")
        assert order.index("source_x") < order.index("mid_y")
        assert order.index("source_y") < order.index("mid_x")
        assert order.index("source_y") < order.index("mid_y")
        assert order.index("mid_x") < order.index("result")
        assert order.index("mid_y") < order.index("result")

    def test_disconnected(self):
        _register_test_nodes()
        NODES.clear()
        _register_test_nodes()
        node(id="a", kind=NodeKind.source)(lambda: None)
        node(id="b", kind=NodeKind.source)(lambda: None)
        node(id="c", kind=NodeKind.source)(lambda: None)
        order = topo_sort(["c", "a", "b"])
        assert set(order) == {"a", "b", "c"}

    def test_cycle_detected(self):
        NODES.clear()

        @node(id="a", kind=NodeKind.derived, deps=["b"])
        def a(b): return "a", "t"

        @node(id="b", kind=NodeKind.derived, deps=["a"])
        def b(a): return "b", "t"

        with pytest.raises(ValueError, match="Cycle"):
            topo_sort(["a", "b"])


class TestStaleness:
    def test_stale_source_changed(self):
        _register_test_nodes()
        sa_old = insert_source_value(RID, "source_a", "a_old", "test_a")
        insert_user_input(RID, "corrected_address", "c1")
        dep_versions = {"source_a": sa_old, "corrected_address": 1}
        insert_source_value(RID, "source_a", "a_new", "test_a")
        assert _is_stale(RID, dep_versions) is True

    def test_fresh_no_change(self):
        _register_test_nodes()
        insert_source_value(RID, "source_a", "a1", "test_a")
        insert_user_input(RID, "corrected_address", "c1")
        import houses.model.persistence as per

        sa_latest = per.get_latest_source_value(RID, "source_a")
        uc_latest = per.get_current_user_input(RID, "corrected_address")
        dep_versions = {"source_a": sa_latest.row_id, "corrected_address": uc_latest.row_id}
        assert _is_stale(RID, dep_versions) is False

    def test_stale_dep_now_present(self):
        _register_test_nodes()
        dep_versions: dict[str, int | None] = {"source_a": None, "corrected_address": None}
        assert _is_stale(RID, dep_versions) is False
        insert_source_value(RID, "source_a", "a1", "test_a")
        assert _is_stale(RID, dep_versions) is True


class TestResolve:
    async def test_resolve_source_node(self):
        _register_test_nodes()
        insert_source_value(RID, "source_a", "hello", "test_a")
        results = await resolve_property(RID, ["source_a"])
        assert results["source_a"].value == "hello"

    async def test_resolve_user_input_node(self):
        _register_test_nodes()
        insert_user_input(RID, "corrected_address", "user_val")
        results = await resolve_property(RID, ["corrected_address"])
        assert results["corrected_address"].value == "user_val"

    async def test_resolve_derived_fresh(self):
        _register_test_nodes()
        sa_id = insert_source_value(RID, "source_a", "a1", "test_a")
        uc_id = insert_user_input(RID, "corrected_address", "c1")
        dr = DerivedRow(
            value="d(a1,c1)",
            dep_versions={"source_a": sa_id, "corrected_address": uc_id},
            source="computed",
            error=None,
            updated_at=datetime(2025, 6, 1, 12, 0, 0),
        )
        save_derived(RID, "derived_d", dr)
        results = await resolve_property(RID, ["derived_d"])
        assert results["derived_d"].value == "d(a1,c1)"
        assert results["derived_d"].source == "computed"

    async def test_resolve_derived_stale_recomputes(self):
        _register_test_nodes()
        sa_old = insert_source_value(RID, "source_a", "a1", "test_a")
        uc_id = insert_user_input(RID, "corrected_address", "c1")
        dr = DerivedRow(
            value="d(a1,c1)",
            dep_versions={"source_a": sa_old, "corrected_address": uc_id},
            source="computed",
            error=None,
            updated_at=datetime(2025, 6, 1, 12, 0, 0),
        )
        save_derived(RID, "derived_d", dr)
        insert_source_value(RID, "source_a", "a2", "test_a")
        results = await resolve_property(RID, ["source_a", "derived_d"])
        assert results["derived_d"].value == "d(a2,c1)"

    async def test_best_address_priority(self):
        _register_test_nodes()
        NODES.clear()

        @node(id="corrected_address", kind=NodeKind.user_input, user_table="user_corrected_address")
        def _(): pass

        @node(id="rightmove_address", kind=NodeKind.source, provenance_template="rightmove_scraper")
        def _(): pass

        @node(id="best_address", kind=NodeKind.derived, deps=["corrected_address", "rightmove_address"])
        def best_address(corrected_address: str | None, rightmove_address: str | None) -> tuple[str | None, str]:
            if corrected_address:
                return corrected_address, "user"
            return rightmove_address, "rightmove_scraper"

        insert_source_value(RID, "rightmove_address", "RM Address", "rightmove_scraper")
        insert_user_input(RID, "corrected_address", "User Address")
        results = await resolve_property(RID, ["best_address"])
        assert results["best_address"].value == "User Address"

    async def test_best_address_fallsback_to_rightmove(self):
        _register_test_nodes()
        NODES.clear()

        @node(id="corrected_address", kind=NodeKind.user_input, user_table="user_corrected_address")
        def _(): pass

        @node(id="rightmove_address", kind=NodeKind.source, provenance_template="rightmove_scraper")
        def _(): pass

        @node(id="best_address", kind=NodeKind.derived, deps=["corrected_address", "rightmove_address"])
        def best_address(corrected_address: str | None, rightmove_address: str | None) -> tuple[str | None, str]:
            if corrected_address:
                return corrected_address, "user"
            return rightmove_address, "rightmove_scraper"

        insert_source_value(RID, "rightmove_address", "RM Address", "rightmove_scraper")
        results = await resolve_property(RID, ["best_address"])
        assert results["best_address"].value == "RM Address"

    async def test_map_url_format(self):
        _register_test_nodes()
        NODES.clear()
        _register_test_nodes()
        NODES.clear()

        @node(id="best_lat", kind=NodeKind.source, provenance_template="test")
        def _(): pass

        @node(id="best_lng", kind=NodeKind.source, provenance_template="test")
        def _(): pass

        @node(id="map_url", kind=NodeKind.derived, deps=["best_lat", "best_lng"])
        def map_url(best_lat: float | None, best_lng: float | None):
            if best_lat is not None and best_lng is not None:
                return f"https://www.google.com/maps?q={best_lat},{best_lng}", "computed"
            return None, ""

        insert_source_value(RID, "best_lat", "51.5", "test")
        insert_source_value(RID, "best_lng", "-0.1", "test")
        results = await resolve_property(RID, ["map_url"])
        assert results["map_url"].value == "https://www.google.com/maps?q=51.5,-0.1"


class TestCheckStaleness:
    async def test_stale_spinner_on_detail_page(self):
        _register_test_nodes()
        sa_old = insert_source_value(RID, "source_a", "a1", "test_a")
        uc_id = insert_user_input(RID, "corrected_address", "c1")
        dr = DerivedRow(
            value="d(a1,c1)",
            dep_versions={"source_a": sa_old, "corrected_address": uc_id},
            source="computed",
            error=None,
            updated_at=datetime(2025, 6, 1, 12, 0, 0),
        )
        save_derived(RID, "derived_d", dr)
        insert_source_value(RID, "source_a", "a2", "test_a")
        stale = check_staleness(RID, ["derived_d"])
        assert stale["derived_d"] is True
        await resolve_property(RID, ["source_a", "derived_d"])
        stale = check_staleness(RID, ["derived_d"])
        assert stale["derived_d"] is False
