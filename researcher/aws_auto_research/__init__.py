"""aws_auto_research — AI Researcher runtime.

Two modules:
  * experiment_runners — run ONE experiment on a backend (local | ec2)
  * researcher         — the autonomous loop + step mechanics

Shared primitives live at the top level (config, control_plane, rates, metrics, result, backends, aws).
"""

from . import solution_user_agent  # noqa: F401 - must be first: tags AWS SDK calls (SO0382)

__version__ = "0.1.0"
