"""Vertical merge — combine adjacent rows with identical lon spans."""

from __future__ import annotations

from tools.commute.tile import Rect, merge_rectangles


def test_merges_adjacent_same_span_rows():
    rects = [
        Rect(51.0, 51.1, -1.0, -0.7),
        Rect(51.1, 51.2, -1.0, -0.7),
        Rect(51.2, 51.3, -1.0, -0.7),
    ]
    assert merge_rectangles(rects) == [Rect(51.0, 51.3, -1.0, -0.7)]


def test_does_not_merge_different_spans():
    rects = [
        Rect(51.0, 51.1, -1.0, -0.7),
        Rect(51.1, 51.2, -0.7, -0.4),
    ]
    assert merge_rectangles(rects) == rects


def test_does_not_merge_across_gap():
    rects = [
        Rect(51.0, 51.1, -1.0, -0.7),
        Rect(51.2, 51.3, -1.0, -0.7),  # row gap: lat 51.1-51.2 missing
    ]
    assert merge_rectangles(rects) == rects


def test_mixed_merge_deterministic_and_disjoint():
    rects = [
        Rect(51.0, 51.1, -1.0, -0.7),
        Rect(51.1, 51.2, -1.0, -0.7),
        Rect(51.2, 51.3, -1.0, -0.7),
        Rect(51.0, 51.1, -0.7, -0.4),
        Rect(51.1, 51.3, -0.7, -0.4),
    ]
    merged = merge_rectangles(rects)
    assert merged == merge_rectangles(rects)  # deterministic
    assert merged == [
        Rect(51.0, 51.3, -1.0, -0.7),  # tall west band
        Rect(51.0, 51.3, -0.7, -0.4),  # tall east band
    ]
    for i, a in enumerate(merged):
        for b in merged[i + 1 :]:
            overlap = not (
                a.lat_max <= b.lat_min
                or b.lat_max <= a.lat_min
                or a.lon_max <= b.lon_min
                or b.lon_max <= a.lon_min
            )
            assert not overlap
