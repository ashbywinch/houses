"""Deploy the Apps Script (gas/ViewTab.gs) to a Google Sheet.

Usage:
    uv run python scripts/deploy_script.py                          # test sheet
    uv run python scripts/deploy_script.py --production             # production sheet
"""

import json
import os
import sys

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from houses.config import settings  # noqa: E402

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/script.projects",
]

SCRIPT_FILE = os.path.join(os.path.dirname(__file__), "..", "gas", "ViewTab.gs")


def _get_sheet_id() -> str:
    if "--production" in sys.argv:
        return settings.sheet_id
    return settings.test_sheet_id


def _get_or_create_script(sheets_service, script_service, sheet_id: str) -> str:
    """Return the Apps Script project ID bound to the spreadsheet."""
    try:
        # Get metadata about the spreadsheet — it includes the linked script ID
        meta = sheets_service.spreadsheets().get(
            spreadsheetId=sheet_id, fields="namedRanges"
        ).execute()
    except Exception:
        pass

    # Try to find an existing project by checking the content
    # The script ID for a bound script is often the spreadsheet ID in reverse
    # or we can list projects
    try:
        # Try to get project content using spreadsheet ID as script ID
        content = script_service.projects().getContent(
            scriptId=sheet_id
        ).execute()
        print(f"Found existing script project: {sheet_id}")
        return sheet_id
    except Exception:
        pass

    # Need to create a new project bound to the spreadsheet
    project = script_service.projects().create(
        body={
            "title": "ViewTab",
            "parentId": sheet_id,
        }
    ).execute()
    pid = project["scriptId"]
    print(f"Created new script project: {pid}")
    return pid


def _upload_script(script_service, script_id: str):
    with open(SCRIPT_FILE) as f:
        code = f.read()

    body = {
        "files": [{
            "name": "ViewTab",
            "type": "SERVER_JS",
            "source": code,
        }],
    }
    result = script_service.projects().updateContent(
        scriptId=script_id, body=body
    ).execute()
    print(f"Uploaded script ({len(code)} bytes)")


def _create_deployment(script_service, script_id: str):
    # First list existing deployments
    existing = script_service.projects().deployments().list(
        scriptId=script_id
    ).execute()
    deployments = existing.get("deployments", [])
    for dep in deployments:
        print(f"  Existing deployment: {dep.get('deploymentId')} — {dep.get('entryPoints', [{}])[0].get('functionName', '?')}")

    # Create a new deployment
    deployment = script_service.projects().deployments().create(
        scriptId=script_id,
        body={
            "versionNumber": 1,
            "manifestFileName": "appsscript",
            "description": "GETURL custom function deployment",
        },
    ).execute()
    dep_id = deployment.get("deploymentId", "?")
    print(f"Created deployment: {dep_id}")


def main():
    sheet_id = _get_sheet_id()
    print(f"Target sheet: {sheet_id}")

    creds = Credentials.from_service_account_info(
        json.loads(settings.service_account_json), scopes=SCOPES
    )
    sheets_service = build("sheets", "v4", credentials=creds)
    script_service = build("script", "v1", credentials=creds)

    script_id = _get_or_create_script(sheets_service, script_service, sheet_id)
    _upload_script(script_service, script_id)
    _create_deployment(script_service, script_id)

    print(f"\nGETURL is now available on sheet {sheet_id}")
    print("Run refresh-formulas to update formulas to use it.")


if __name__ == "__main__":
    main()
