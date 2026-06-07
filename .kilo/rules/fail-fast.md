# Fail Fast — Don't Paper Over Errors

When a function, module, or enrichment pipeline fails to produce expected results, make it visible immediately. Do not silently degrade, return empty values, or continue as if nothing happened unless the user explicitly authorizes graceful degradation.

## What This Means

- **Log what went wrong** — When extraction, enrichment, or any operation fails to find expected data, log which fields are missing and which were found. A silent partial result is indistinguishable from a successful one.
- **Don't swallow errors** — `except: pass`, unconditional fallbacks that discard the error, and returning empty dicts without explanation all hide failures. If an error occurs, log it with enough context to diagnose.
- **Make failures discoverable** — A user running a script or hitting an endpoint should be able to tell from the output that something didn't work, not just from digging through log files. Include warnings in API responses where feasible.
- **Graceful degradation must be explicit** — The default is to report failure. If the user wants the system to continue silently despite errors, they must opt in (e.g., a query parameter, config flag).

## Examples

Good:
```python
result = scrape(url)
missing = [k for k in EXPECTED if k not in result]
if missing:
    logger.warning("Scraper for %s: missing %s, found %s", url, missing, list(result))
```

Bad:
```python
result = scrape(url)  # silently returns {} with no indication why
```
