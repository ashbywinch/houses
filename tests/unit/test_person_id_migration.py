"""Unit tests for the person-id migration transform (scripts/backfill_person_ids.py).

The transform is pure — these pin the re-key contract without touching
any database: name-keyed node ids and dep timestamps re-key to the
person id, and works_estimates value dicts keep their money under the
stable key a rename could otherwise orphan.
"""

from __future__ import annotations

import json
import zlib

import pytest

from scripts.backfill_person_ids import collect_mapping, remap_row

MAPPING = {"Simon": "1", "Lorena": "2"}

# lucidlint: ignore record-shape the test payload IS an opaque wire fixture — typed loosely on purpose
def blob(payload: dict) -> bytes:
    return zlib.compress(json.dumps(payload).encode())
def test_collect_mapping_assigns_numeric_ids_incrementally():
    """Numeric scheme: everyone missing an id gets max(existing)+1, in
    list order — collision-free by construction, no slug dance."""
    mapping = collect_mapping([{"name": "Simon"}, {"name": "Lorena"}, {"name": "George"}])
    assert mapping == {"Simon": "1", "Lorena": "2", "George": "3"}


def test_collect_mapping_keeps_existing_ids_and_continues_past_them():
    mapping = collect_mapping([{"name": "Simon", "person_id": "7"}, {"name": "Lorena"}])
    assert mapping == {"Simon": "7", "Lorena": "8"}


def test_node_id_rekeys_the_person_segment():
    out = remap_row("1234/Simon/Pimlico/walk", {}, b"", MAPPING)
    assert out is not None
    assert out[0] == "1234/1/Pimlico/walk"
    # a label that merely CONTAINS a person name is untouched
    out2 = remap_row("1234/Lorena/Lorena Square/walk", {}, b"", MAPPING)
    assert out2 is not None
    assert out2[0] == "1234/2/Lorena Square/walk"

def test_dep_timestamps_rekey_inner_node_ids():
    dep = {"1234/Simon/Pimlico/poi": "2026-01-01", "unrelated": "2026-01-02"}
    out = remap_row("1234/Simon/Pimlico/final_fuel", dep, b"", MAPPING)
    assert out is not None
    parsed = json.loads(out[1])
    assert "1234/1/Pimlico/poi" in parsed
    assert parsed["unrelated"] == "2026-01-02"


def test_works_estimates_value_keys_follow_the_id():
    row = blob({"status": "succeeded", "value": {"Ashby": {"amount": "25000.00", "currency": "GBP"}}})
    out = remap_row("42345678/works_estimates", {}, row, {"Ashby": "3"})
    assert out is not None
    payload = json.loads(zlib.decompress(out[2]).decode())
    assert payload["value"]["3"]["amount"] == "25000.00"
    assert "Ashby" not in payload["value"]


def test_unrelated_rows_are_untouched():
    assert remap_row("9999/best_address", {}, b"", MAPPING) is None

def test_label_segment_that_equals_a_person_name_is_not_rekeyed():
    """Position-2 anchoring: a POI label that happens to be a person's
    name (a destination called ``Dad`` while a person is named Dad) must
    stay a label — only the person segment re-keys."""
    mapping = {"Simon": "1", "Dad": "9"}
    out = remap_row("1234/Simon/Dad/place", {}, b"", mapping)
    assert out is not None
    assert out[0] == "1234/1/Dad/place"
    dep = {"1234/Simon/Dad/poi": "2026-01-01"}
    out2 = remap_row("1234/Simon/Dad/final_fuel", dep, b"", mapping)
    assert out2 is not None
    assert json.loads(out2[1]) == {"1234/1/Dad/poi": "2026-01-01"}


def test_step_segment_that_equals_a_person_name_is_not_rekeyed():
    """A person named ``walk`` must not re-key the step segment of every
    commute node id — only position 2 is the person."""
    mapping = {"Simon": "1", "Walk": "5"}
    out = remap_row("1234/Simon/Pimlico/walk", {}, b"", mapping)
    assert out is not None
    assert out[0] == "1234/1/Pimlico/walk", out[0]


def test_non_person_position_two_segment_is_untouched():
    """Property node ids carry node NAMES in position 2 (``best_address``,
    ``works_estimates``) — never re-keyed even if a name overlaps."""
    assert remap_row("1234/best_address", {}, b"", {"best_address": "1", "walk": "5"}) is None
    assert remap_row("1234/works_estimates", {}, b"", {"works_estimates": "2"}) is None
    assert remap_row("9999/best_address", {}, b"", MAPPING) is None


def test_collect_mapping_keeps_pre_migration_and_numbered_ids():
    """A legacy slug id is kept (it is already stable in the DB), and
    everyone missing an id gets the next fresh NUMERIC id — the slug is
    never the assignment scheme."""
    persons = [
        {"name": "Simon", "person_id": "p_simon"},
        {"name": "Lorena"},
        {"name": "George"},
    ]
    mapping = collect_mapping(persons)
    assert mapping["Simon"] == "p_simon"
    assert mapping["Lorena"] == "1"
    assert mapping["George"] == "2"


def test_works_slug_keyed_row_remaps_to_the_id():
    """Writes between the code cutover and the migration store the slug
    fallback key (``ashby``) — the remap must resolve it to the numeric
    id, never orphan it."""
    row = blob({"status": "succeeded", "value": {"ashby": {"amount": "25000.00", "currency": "GBP"}}})
    out = remap_row("42345678/works_estimates", {}, row, {"Ashby": "3"})
    assert out is not None
    payload = json.loads(zlib.decompress(out[2]).decode())
    assert payload["value"]["3"]["amount"] == "25000.00"
    assert "ashby" not in payload["value"]

def test_slug_segment_node_id_resolves():
    """Rows written by the running app between the code cutover and the
    migration carry the slug in the person segment (``simon``) — the
    re-key must resolve it to the numeric id, not leave it remappable."""
    out = remap_row("1234/simon/Pimlico/walk", {}, b"", MAPPING)
    assert out is not None
    assert out[0] == "1234/1/Pimlico/walk", out[0]
    dep = {"1234/simon/Pimlico/poi": "2026-01-01"}
    out2 = remap_row("1234/simon/Pimlico/final_fuel", dep, b"", MAPPING)
    assert out2 is not None
    assert json.loads(out2[1]) == {"1234/1/Pimlico/poi": "2026-01-01"}

def test_legacy_string_value_dict_is_healed():
    """A sheet-migration row stores the works dict as a JSON STRING —
    the migration parses it, remaps the keys to ids, and a second pass
    finds nothing left (idempotent)."""
    row = blob({"status": "succeeded", "value": '{"Ashby": 25000}'})
    out = remap_row("42345678/works_estimates", {}, row, {"Ashby": "3"})
    assert out is not None
    payload = json.loads(zlib.decompress(out[2]).decode())
    assert payload["value"] == {"3": 25000}
    # second pass: nothing remappable
    assert remap_row("42345678/works_estimates", {}, out[2], {"Ashby": "3"}) is None


def test_corrupt_works_estimates_blob_fails_fast():
    """A works_estimates row the migration cannot parse must ABORT the
    run — silently keeping its old person keys is a swallowed error
    (coding-standards: never swallow errors — fail fast)."""
    with pytest.raises(RuntimeError, match="not parseable"):
        remap_row("123/works_estimates", {}, b"this-is-not-zlib", MAPPING)
