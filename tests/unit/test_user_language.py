"""User-language sweep — plan acceptance: no user-facing UI string contains
the internal words "isochrone", "transit", or "shed".

The plan's naming table maps internal terms to user language (map layers
are "Train: …", "Drive to …", "Where we could live"; the settings page says
"how they get there", not "acceptable modes").  Code, files, endpoints and
docs keep the internal words; the UI never shows them.  This test scans
every .vue template (user-visible markup — identifiers and data keys are
not user text) and the committed commute map artifact.
"""

from __future__ import annotations

import re
from pathlib import Path

FORBIDDEN = re.compile(r"\b(isochrone|transit|shed)\b", re.IGNORECASE)
FRONTEND_SRC = Path(__file__).resolve().parents[2] / "houses" / "frontend" / "src"
MAP_ARTIFACT = Path(__file__).resolve().parents[2] / "data" / "commute" / "commute_map.html"


def _template_of(src: str) -> str:
    m = re.search(r"<template>(.*?)</template>", src, re.DOTALL)
    return m.group(1) if m else ""


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
def test_no_vue_template_uses_internal_words():
    offenders = []
    for vue in sorted(FRONTEND_SRC.rglob("*.vue")):
        if FORBIDDEN.search(_template_of(vue.read_text())):
            offenders.append(str(vue.relative_to(FRONTEND_SRC)))
    assert not offenders, f"user-visible templates use internal words: {offenders}"


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
def test_commute_map_artifact_keeps_user_language():
    assert MAP_ARTIFACT.exists(), f"missing committed map artifact {MAP_ARTIFACT}"
    matches = FORBIDDEN.findall(MAP_ARTIFACT.read_text())
    # "transition" (CSS) is the only plausible false positive — the regex
    # requires a word boundary, so a real match here is a user-facing label.
    assert not matches, f"commute_map.html contains user-visible internal words: {matches}"
