# Data Sources

## GIAS — All Establishments

- **File**: `data/edubaseall_full.csv`
- **Source**: https://www.get-information-schools.service.gov.uk/Downloads
- **Download**: Form POST to `/Downloads/Collate` selecting "Establishment fields CSV"
- **Date**: 3 June 2026
- **Rows**: ~52,401
- **Columns**: 135
- **Note**: Contains all school types (open, closed, proposed). Geocoding and Ofsted data handled separately.

## Ofsted Inspections — Latest Outcomes

- **File**: `data/ofsted_inspections.csv`
- **Source**: https://www.gov.uk/government/statistical-data-sets/monthly-management-information-ofsteds-school-inspections-outcomes
- **Download direct**: https://assets.publishing.service.gov.uk/media/6a06d8adee62840dba48a304/Management_information_-_state-funded_schools_-_latest_inspections_as_at_30_Apr_2026.csv
- **Date**: 30 April 2026 (published 20 May 2026)
- **Rows**: ~21,962
- **Columns**: 85
- **Key columns**: `URN` (join key), `Latest OEIF overall effectiveness` (1=Outstanding, 2=Good, 3=Requires Improvement, 4=Inadequate, NULL=not inspected, Not Judged=ungraded)

## Enriched Schools (Processed)

- **File**: `data/edubaseall_enriched.csv`
- **Process**: Filtered from full edubase, geocoded with lat/lng, merged on URN with Ofsted ratings
- **Columns**: Base GIAS fields + Latitude + Longitude + OfstedRating (name)
