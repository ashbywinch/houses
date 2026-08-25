"""Deploy the Apps Script (gas/ViewTab.gs) to a Google Sheet.

Usage:
    uv run python scripts/deploy_script.py                          # test sheet
    uv run python scripts/deploy_script.py --production             # production sheet
"""

import contextlib
import json
import logging
import os
import sys

from google.oauth2.service_account import Credentials

# google-api-python-client is an optional tool dependency (not in the project
# venv) — imported under a guard so this script still imports for inspection;
# main() fails with a clear message at deploy time when it is missing.
try:
    from googleapiclient.discovery import build  # type: ignore[missing-import]  # optional tool dependency
    # lucidlint: ignore swallow optional tool dependency — main() fails with a clear message when build is None
except ImportError:
    build = None

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from houses.settings import settings

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/script.projects",
]

SCRIPT_FILE = os.path.join(os.path.dirname(__file__), "..", "gas", "ViewTab.gs")


def _get_sheet_id() -> str:
    if "--production" in sys.argv:
        return settings.sheet_id
    return settings.test_sheet_id


def _find_existing_project(script_service, sheet_id: str) -> str | None:
    """The Apps Script project bound to the spreadsheet, or None when not found."""
    try:
        # A bound script's project ID is the spreadsheet's own ID
        script_service.projects().getContent(scriptId=sheet_id).execute()
        print(f"Found existing script project: {sheet_id}")
        return sheet_id
    # lucidlint: ignore broad-except deliberate fallback — the probe treats any failure as 'no script project yet'
    except Exception as e:
        logger.debug("no script project under %s yet — creating one: %s", sheet_id, e)
        return None


def _get_or_create_script(sheets_service, script_service, sheet_id: str) -> str:
    """Return the Apps Script project ID bound to the spreadsheet."""
    with contextlib.suppress(Exception):
        sheets_service.spreadsheets().get(spreadsheetId=sheet_id, fields="namedRanges").execute()

    existing = _find_existing_project(script_service, sheet_id)
    if existing is not None:
        return existing

    # Need to create a new project bound to the spreadsheet
    project = (
        script_service.projects()
        .create(
            body={
                "title": "ViewTab",
                "parentId": sheet_id,
            }
        )
        .execute()
    )
    pid = project["scriptId"]
    print(f"Created new script project: {pid}")
    return pid


def _upload_script(script_service, script_id: str):
    with open(SCRIPT_FILE) as f:
        code = f.read()

    body = {
        "files": [
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
            {
                "name": "ViewTab",
                "type": "SERVER_JS",
                "source": code,
            }
        ],
    }
    script_service.projects().updateContent(scriptId=script_id, body=body).execute()
    print(f"Uploaded script ({len(code)} bytes)")


def _create_deployment(script_service, script_id: str):
    # First list existing deployments
    existing = script_service.projects().deployments().list(scriptId=script_id).execute()
    deployments = existing.get("deployments", [])
    for dep in deployments:
        print(
            f"  Existing deployment: {dep.get('deploymentId')} — {dep.get('entryPoints', [{}])[0].get('functionName', '?')}"  # noqa: E501  # one-off deployment listing print reads better unsplit
        )

    # Create a new deployment
    deployment = (
        script_service.projects()
        .deployments()
        .create(
            scriptId=script_id,
            body={
                "versionNumber": 1,
                "manifestFileName": "appsscript",
                "description": "GETURL custom function deployment",
            },
        )
        .execute()
    )
    dep_id = deployment.get("deploymentId", "?")
    print(f"Created deployment: {dep_id}")


def main():
    if build is None:
        raise SystemExit("google-api-python-client is not installed — run: uv add google-api-python-client")
    sheet_id = _get_sheet_id()
    print(f"Target sheet: {sheet_id}")

    creds = Credentials.from_service_account_info(json.loads(settings.service_account_json), scopes=SCOPES)
    sheets_service = build(serviceName="sheets", version="v4", credentials=creds)
    script_service = build(serviceName="script", version="v1", credentials=creds)

    script_id = _get_or_create_script(sheets_service, script_service, sheet_id)
    _upload_script(script_service, script_id)
    _create_deployment(script_service, script_id)

    print(f"\nGETURL is now available on sheet {sheet_id}")
    print("Run refresh-formulas to update formulas to use it.")


if __name__ == "__main__":
    main()
