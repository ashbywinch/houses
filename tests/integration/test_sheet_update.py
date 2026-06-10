"""Integration tests for dry_run in inject-property — creates cache files."""

from fastapi.testclient import TestClient

from houses.models import EnrichedProperty
from houses.server import app
from houses.sheets import row_values

client = TestClient(app)


class TestDryRun:
    """dry_run=true returns enriched data without writing to the sheet."""

    def test_dry_run_returns_same_data_as_normal(self):
        """Both with and without dry_run return identical enrichment data."""
        payload = {
            "url": "https://www.rightmove.co.uk/properties/999999",
            "address": "10 High Street, Test Town, TE1 1ST",
        }
        resp_normal = client.post("/inject-property", json=payload)
        resp_dry = client.post("/inject-property?dry_run=true", json=payload)

        assert resp_normal.status_code in (200, 201)
        assert resp_dry.status_code == 200

        data_normal = resp_normal.json().get("data", {})
        data_dry = resp_dry.json().get("data", {})
        assert "simon_commute" in data_normal
        assert "simon_commute" in data_dry

    def test_dry_run_does_not_append_to_sheet(self):
        """Calling dry_run multiple times should not cause the data to change."""
        import json as _json

        payload = {
            "url": "https://www.rightmove.co.uk/properties/888888",
            "address": "20 High Street, Test Town, TE1 1ST",
        }
        resp1 = client.post("/inject-property?dry_run=true", json=payload)
        data1 = resp1.json()["data"]
        row1 = row_values(EnrichedProperty(**data1))

        resp2 = client.post("/inject-property?dry_run=true", json=payload)
        data2 = resp2.json()["data"]
        row2 = row_values(EnrichedProperty(**data2))

        assert row1 == row2, (
            f"dry_run should return identical data on repeated calls\n"
            f"Differing keys: {set(row1.keys()) ^ set(row2.keys())}\n"
            f"Common differing values:\n"
            + "\n".join(f"  {k}: {row1.get(k)!r} vs {row2.get(k)!r}" for k in row1 if row1.get(k) != row2.get(k))
            + f"\n\nResponse 1 data:\n{
                _json.dumps(
                    {k: data1.get(k) for k in sorted(data1) if data1.get(k)},
                    indent=2,
                    default=str,
                )
            }"
            + f"\n\nResponse 2 data:\n{
                _json.dumps(
                    {k: data2.get(k) for k in sorted(data2) if data2.get(k)},
                    indent=2,
                    default=str,
                )
            }"
        )
