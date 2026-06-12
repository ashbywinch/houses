# Rail Fare Fallback

When TfL doesn't return fare data, estimates cost from a CSV of National
Rail fares to London terminals. Data extracted from the NR DTD feed by
`scripts/extract_rail_fares.py`.

- **Module**: `houses/rail_fares.py` → `nearest_station()`, `fare_between()`
- **Data**: `data/rail_fares.csv`
