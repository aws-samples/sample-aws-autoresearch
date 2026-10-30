"""aws_auto_research — AI Researcher runtime.

Two modules:
  * experiment_runners — run ONE experiment on a backend (local | ec2)
  * researcher         — the autonomous loop + step mechanics

Shared primitives live at the top level (config, control_plane, rates, metrics, result, backends, aws).
"""

__version__ = "0.1.0"
