"""One-off data migration: persons' acceptable_modes 'train' -> 'transit'.

The mode value was renamed to match what it actually gates (the full TfL
transit journey — rail, tube, DLR, overground AND bus legs). Persisted
persons nodes still hold 'train'; without this rewrite those POIs become
unroutable by transit (the selector checks for 'transit').

Run (deliberate data fix — the guarded write path requires the opt-in):
    HOUSES_SCRIPTS_MAY_WRITE=1 uv run python tools/migrate_train_to_transit.py
"""
from dataclasses import replace

from houses.model.domain import Person, PlaceOfInterest
from houses.services_provider import get_services


def migrate() -> int:
    svc = get_services()
    attempt = svc.persons_source.latest_attempt()
    persons = attempt.value_or_none() or []
    changed_pois = 0
    migrated_persons = []
    for p in persons:
        if not isinstance(p, Person):
            continue
        pois = []
        for poi in p.places_of_interest:
            if not isinstance(poi, PlaceOfInterest):
                pois.append(poi)
                continue
            modes = tuple("transit" if m == "train" else m for m in poi.acceptable_modes)
            if modes != poi.acceptable_modes:
                changed_pois += 1
            pois.append(replace(poi, acceptable_modes=modes))
        if pois != list(p.places_of_interest):
            migrated_persons.append(replace(p, places_of_interest=tuple(pois)))
        else:
            migrated_persons.append(p)
    if changed_pois:
        svc.persons_source.push(migrated_persons, "migration")
    return changed_pois


if __name__ == "__main__":
    n = migrate()
    print(f"{'migrated' if n else 'nothing to migrate'}: {n} POIs rewritten")
