"""Control-plane client — the agent's only path to project/experiment state.

Wraps the AutoResearch-agent_api Lambda (the agent has no direct DynamoDB access), replacing
agent-api.sh + the per-script jq parsing. Returns a validated Project model for the fields the
runners read.
"""

from __future__ import annotations

import json
import os

import boto3
from botocore.config import Config
from pydantic import BaseModel, ConfigDict

_CFG = Config(retries={"mode": "adaptive", "max_attempts": 5})
_FUNCTION = "AutoResearch-agent_api"


class Project(BaseModel):
    model_config = ConfigDict(extra="allow")  # tolerate unknown/new record fields

    project_id: str
    s3_data: str = ""
    experiment_runner: str = "ec2"
    worker_instance_ids: list[str] = []
    worker_instance_type: str = ""
    researcher_instance_type: str = ""
    ssh_key_param: str = ""
    status: str = "unknown"
    metric_name: str = ""
    # loop fields (stored as strings in the record)
    budget_usd: str = "0"
    spent_usd: str = "0"
    researcher_cost_usd: str = "0"
    patience: str = "10"
    experiment_timeout_minutes: int = 15
    write_paper: bool = False
    best_metric: str = ""


class ControlPlaneClient:
    def __init__(self, region: str):
        self._lam = boto3.client("lambda", region_name=region, config=_CFG)

    def _invoke(self, payload: dict) -> dict:
        # Per-project capability token (from SSM, exported at boot as AR_AGENT_TOKEN) binds this
        # caller to its project_id in agent_api. Absent for legacy projects → agent_api allows.
        token = os.environ.get("AR_AGENT_TOKEN")
        if token and "_token" not in payload:
            payload = {**payload, "_token": token}
        try:
            resp = self._lam.invoke(
                FunctionName=_FUNCTION,
                InvocationType="RequestResponse",
                Payload=json.dumps(payload).encode("utf-8"),
            )
        except Exception as e:  # noqa: BLE001 — network/permission
            return {"ok": False, "error": f"agent-api invoke failed: {e}"}
        body = resp["Payload"].read().decode("utf-8")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"ok": False, "error": f"non-JSON response: {body[:200]}"}

    def invoke_raw(self, payload: dict) -> dict:
        """Generic passthrough (any action) — backs the `aws-research api` command / agent-api.sh."""
        return self._invoke(payload)

    def get_project(self, project_id: str) -> Project:
        r = self._invoke({"action": "get_project", "project_id": project_id})
        if not r.get("project"):
            raise RuntimeError(f"get_project failed: {r.get('error', r)}")
        return Project.model_validate(r["project"])

    def update_project(self, project_id: str, updates: dict) -> dict:
        return self._invoke({"action": "update_project", "project_id": project_id, "updates": updates})

    def list_experiments(self, project_id: str, states: list[str] | None = None) -> list[dict]:
        payload = {"action": "list_experiments", "project_id": project_id}
        if states:
            payload["states"] = states
        return self._invoke(payload).get("experiments", []) or []

    def create_experiment(self, project_id: str, experiment_id: str, item: dict) -> dict:
        return self._invoke({"action": "create_experiment", "project_id": project_id,
                             "experiment_id": experiment_id, "item": item})

    def update_experiment(self, project_id: str, experiment_id: str, updates: dict) -> dict:
        return self._invoke({"action": "update_experiment", "project_id": project_id,
                             "experiment_id": experiment_id, "updates": updates})

    def get_guidance(self, project_id: str) -> list[dict]:
        return self._invoke({"action": "get_guidance", "project_id": project_id}).get("guidance", []) or []

    def mark_guidance_processed(self, project_id: str, guidance_id: str) -> dict:
        return self._invoke({"action": "mark_guidance_processed", "project_id": project_id,
                             "guidance_id": guidance_id})
