"""mitmproxy-based HTTP replay transport (prototype).

See ``docs/claude/design/http-replay-mitmproxy-prototype.md`` for the
architecture, scope, and follow-up work needed to make this production.
"""

from clm.infrastructure.http_replay_mitm.proxy_manager import MitmproxyManager

__all__ = ["MitmproxyManager"]
