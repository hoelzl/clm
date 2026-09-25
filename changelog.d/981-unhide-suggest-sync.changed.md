- `clm slides suggest-sync` is a first-class verb on `clm slides --help`
  again (#981). It had been hidden as "plumbing" for the pre-split layout —
  but the unified bilingual layout (both languages in one `slides_x.py` /
  `.cs`) is still authored: the whole C# course (236 decks) and part of the
  Python course (59 decks) use it, and `clm slides sync` cannot read a unified
  deck. The help text and `clm info commands` now state the boundary: unified
  deck → `suggest-sync` (read-only suggestions vs git HEAD, MCP
  `slides_suggest_sync`); split pair → `sync` (ledger-backed reconciliation
  that writes, MCP `slides_sync_report`). No behaviour change.
