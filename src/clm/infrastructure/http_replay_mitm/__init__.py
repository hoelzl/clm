"""mitmproxy-based HTTP replay transport.

Opt-in alternative to the in-process vcrpy transport, selected with
``CLM_HTTP_REPLAY_TRANSPORT=mitmproxy`` (vcrpy remains the default). See
``docs/claude/issue-165-production-plan.md`` for the phased production plan
(P1-P4 shipped: vcrpy-YAML bridge, request routing, correctness/security
parity, and Docker worker support).
"""

from clm.infrastructure.http_replay_mitm.proxy_manager import MitmproxyManager

__all__ = ["MitmproxyManager"]
