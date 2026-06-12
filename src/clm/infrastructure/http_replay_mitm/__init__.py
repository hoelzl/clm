"""mitmproxy-based HTTP replay transport.

CLM's HTTP-replay transport (issue #165; the legacy in-process vcrpy
transport was removed in #355). mitmproxy matches repeated and concurrent
identical requests that vcrpy's consume-once model mishandled, and requires
Python >=3.12. See ``docs/claude/issue-165-production-plan.md`` for the
phased production plan (P1-P4 shipped: vcrpy-YAML cassette format bridge,
request routing, correctness/security parity, and Docker worker support).
"""

from clm.infrastructure.http_replay_mitm.proxy_manager import MitmproxyManager

__all__ = ["MitmproxyManager"]
