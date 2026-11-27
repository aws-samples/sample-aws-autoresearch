"""extensions: the domain extensions installed in this deployment (GET /extensions), for the web app's Create
Project form and Extensions page. Read-only; manifests are installed per deployment by an
AutoResearchExtension-<name> stack (design.md "Domain extensions"). Only public fields are returned."""

import solution_user_agent  # noqa: F401 - must be first: tags AWS SDK calls (SO0382)
import json
import os

import boto3

import ar_extensions

s3 = boto3.client("s3")
DATA_BUCKET = os.environ["DATA_BUCKET"]


def _resp(code, body):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body, default=str)}


def handler(event, context):
    try:
        return _resp(200, {"extensions": ar_extensions.list_installed(s3, DATA_BUCKET)})
    except Exception as e:  # noqa: BLE001
        print(f"extensions: {e!r}")
        return _resp(500, {"error": "Could not list the installed extensions."})
