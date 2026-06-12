# Schools

Finds nearest primary and secondary schools accepting boys,
non-fee-paying, within configured search radius. If the closest
secondary is girls-only, substitutes the nearest mixed/boys alternative.

- **Module**: `houses/schools.py` → `compute_school_commute()`,
  `find_nearest()`
- **Data**: GIAS CSV + Ofsted ratings (`data/edubaseall_enriched.csv`)
