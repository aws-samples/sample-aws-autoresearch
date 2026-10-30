"""Runner selection — add a runner = add a class + one entry here."""

from __future__ import annotations

from ..config import Settings
from ..control_plane import ControlPlaneClient
from .base import ExperimentRunner
from .ec2 import EC2Runner
from .local import LocalRunner

_RUNNERS: dict[str, type[ExperimentRunner]] = {
    "ec2": EC2Runner,
    "local": LocalRunner,
}


def runner_for(settings: Settings, client: ControlPlaneClient) -> ExperimentRunner:
    """Build the runner named by the project's experiment_runner field (default ec2)."""
    name = client.get_project(settings.project_id).experiment_runner or "ec2"
    cls = _RUNNERS.get(name, EC2Runner)
    return cls(settings, client)
