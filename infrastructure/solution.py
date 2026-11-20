"""AWS Solution identity - single source of truth for usage-metrics instrumentation.

SOLUTION_VERSION must match the released version (keep in sync with researcher/pyproject.toml).
"""

SOLUTION_ID = "SO0382"
SOLUTION_NAME = "AWS Autoresearch"
SOLUTION_VERSION = "v0.1.0"
USER_AGENT = f"AWSSOLUTION/{SOLUTION_ID}/{SOLUTION_VERSION}"


def description(suffix: str = "", component: str = "") -> str:
    """CloudFormation Description: "(SOxxxx[suffix]) Name[ component], vX.Y.Z".
    Only the main deployment template uses the bare Solution ID; all others pass a suffix."""
    name = f"{SOLUTION_NAME} {component}".strip()
    return f"({SOLUTION_ID}{suffix}) {name}, {SOLUTION_VERSION}"
