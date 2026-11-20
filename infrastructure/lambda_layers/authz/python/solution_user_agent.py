"""Process-wide AWS SDK user-agent tagging for AWS Solution usage metrics.

Appends the solution token (AWSSOLUTION/<SOLUTION_ID>/<SOLUTION_VERSION>), taken from the
USER_AGENT_STRING env var, as its own space-delimited section of every botocore client's User-Agent.
Import this BEFORE any module that creates an AWS client. No-op when the env var is unset.
"""

import os

import botocore.session

SOLUTION_UA = os.environ.get("USER_AGENT_STRING", "").strip()

if SOLUTION_UA and not getattr(botocore.session.Session.__init__, "_solution_ua", False):
    _orig_init = botocore.session.Session.__init__

    def _init(self, *args, **kwargs):
        _orig_init(self, *args, **kwargs)
        extra = self.user_agent_extra or ""
        if SOLUTION_UA not in extra.split():
            self.user_agent_extra = f"{extra} {SOLUTION_UA}".strip()

    _init._solution_ua = True
    botocore.session.Session.__init__ = _init
