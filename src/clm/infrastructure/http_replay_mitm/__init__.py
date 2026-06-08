"""mitmproxy-based HTTP replay transport.

The **default** HTTP-replay transport (issue #165); the in-process vcrpy
transport is the opt-out, selected with ``CLM_HTTP_REPLAY_TRANSPORT=vcrpy``.
mitmproxy matches repeated and concurrent identical requests that vcrpy's
consume-once model mishandles, and requires Python >=3.12. See
``docs/claude/issue-165-production-plan.md`` for the phased production plan
(P1-P4 shipped: vcrpy-YAML bridge, request routing, correctness/security
parity, and Docker worker support).
"""

from clm.infrastructure.http_replay_mitm.proxy_manager import MitmproxyManager

__all__ = ["MitmproxyManager"]
