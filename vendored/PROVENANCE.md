# Vendored: uk-property-apis 0.1.0

`uk_property_apis-0.1.0-py3-none-any.whl` (+ the matching `.tar.gz` sdist) is
**vendored, not upstreamed**. Deal with it as a file, not a git dependency.

## Why it is here

- Commit `88f19329` (2026-06-04) added
  `uk-property-apis = { git = github.com/kubilay-yavuz/uk-property-intel.git, subdirectory = packages/apis }`
  (MIT, per its metadata) for the VOA council-tax scraping client
  (`houses/council_tax.py` → `uk_property_apis.voa.VOAClient`). The pin was
  `8a18d9209216ef2144eef64ef80de7c80194468e`, never bumped.
- On 2026-10-06 the upstream repository became unreachable — GitHub returns
  `404` for it, `git ls-remote` reports *Repository not found*, and nothing
  matches it on GitHub search, so it was deleted or made private by its author.
  It was never published to PyPI. The account `kubilay-yavuz` still exists.
- That is what turned CI red: `uv sync` on a clean runner must clone the exact
  pinned commit, and the URL answers nothing. It worked on machines that had
  already installed the package because the installed entry satisfies the lock.

## Where this copy came from

The last known-good source was preserved from the local virtualenv the night
the repo vanished (`site-packages/uk_property_apis` + its dist-info metadata —
the wheel-flattened 0.1.0 build of commit `8a18d920`). That copy is the ONLY
source; the uv git cache was already empty. From it:

1. `uk_property_apis/` (74 `.py` files) copied verbatim (no `__pycache__`).
2. `pyproject.toml` written from the installed `METADATA` (name, version,
   `requires-python >=3.12,<3.14`, deps httpx/pydantic/selectolax/tenacity).
3. `LICENSE` — MIT text, author Kubilay Yavuz per the metadata; the upstream
   tree itself carried no licence file or headers.
4. Built with `uv build` (setuptools) → the wheel + sdist here.

## Rebuilding / updating

- Rebuild: unpack the sdist, or re-build from a fresh copy of the preserved
  source, then `uv build` and replace both files.
- Only do that from a source you trust: this vendored copy is the working
  definition of `uk-property-apis` for this repo. If upstream ever comes back,
  prefer reviewing and re-vendoring from it — but not before verifying the
  contents match 0.1.0 at `8a18d920`.

## Why a wheel, not a source tree

The repo's code-health gate (`make lucidlint`) scans every tracked `.py`
file and enforces zero findings; the vendored tree is third-party code of no
concern to that gate. A `py3-none-any` wheel is the same source, still
inspectable (`unzip -p` the wheel), and is skipped by every language tool.
Trade-off accepted deliberately; the sdist is kept alongside for the same
reason as the wheel's provenance.
