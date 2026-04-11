# Proposals

Design proposals for CLM changes. Each proposal is a standalone Markdown
document describing a problem, the options considered, and the recommended
path forward.

## Layout

- **Top-level (`docs/proposals/*.md`)** — **active** proposals: draft,
  under review, accepted, or partially implemented. These are the ones
  worth inspecting when you want to know what's in flight.
- **`docs/proposals/archive/*.md`** — **completed** proposals whose work
  has landed. Kept for historical context and for the forensic trail
  behind the current architecture, but not part of the active work queue.

When a proposal is fully implemented (or consciously abandoned), update
its status header and `git mv` it into `archive/` so that a glance at
`docs/proposals/` shows only open work. The status header in each archived
document should record the completion date and summarise which items
landed vs. which were dropped and why.
