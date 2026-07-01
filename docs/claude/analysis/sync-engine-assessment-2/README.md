# Sync-Engine Assessment 2 — Evidence Folder

Supporting evidence for **`docs/claude/sync-engine-architecture-assessment-2.md`**
(2026-07-01). Fourteen independent review agents examined the sync machinery at
tree `3980f9ab` (master tip of 2026-07-01, pre-PR-#515): ten *readers*, each
covering one slice (engine internals, identity model, CLI surface, design docs,
prior assessment, tests, GitHub issue history), then four *critics* who argued
assigned positions against the pooled reader evidence and re-verified their
most load-bearing claims directly in the tree.

Caveats: file:line citations were valid at `3980f9ab` and drift with the tree.
Each agent worked independently; where slices disagree, the top-level assessment
adjudicates. Raw structured agent output was converted to markdown mechanically.

| File | Slice | Contents |
|---|---|---|
| [01-engine-sync-plan-core.md](01-engine-sync-plan-core.md) | Engine core: `sync_plan.py` | Full read of the 4,829-line classifier/plan module — data model, identity schemes, pipeline, plan-patching passes |
| [02-engine-sync-apply.md](02-engine-sync-apply.md) | Engine core: `sync_apply.py` + plan walker | Full read of the 4,399-line apply module — decision overlays, ordering invariants, idempotency, partial-success paths |
| [03-identity-model.md](03-identity-model.md) | Identity & alignment model | pairing.py, anchor_primitives.py, sync_semantic.py, sync_companion.py, sync_code.py, reconcile_vo_ids.py — the coexisting identity schemes |
| [04-writeback-projection.md](04-writeback-projection.md) | Write-back, split, and the companion projection | sync_writeback.py, split.py, sync_recover.py, sync_translate.py — projections, atomicity, the inline/extract round-trip |
| [05-cli-agent-surface.md](05-cli-agent-surface.md) | CLI / agent-facing surface | All sync verbs, flags, MCP tools, state stores — surface size, per-verb semantic divergence, item-handle fragility |
| [06-design-docs-identity.md](06-design-docs-identity.md) | Design-doc arc: engine & identity | single-language-authoring-sync, content-anchor-identity, plan-resolve-apply, git-as-baseline — intent vs reality |
| [07-design-docs-toolkit.md](07-design-docs-toolkit.md) | Design-doc arc: toolkit, ledger, companions | agent-toolkit-redesign, consistency-ledger, separated-voiceover-companions, vo-anchoring, hardening — the causal chain of layers |
| [08-prior-assessment.md](08-prior-assessment.md) | The 2026-06-09 assessment re-examined | Verdict B, its remediations, and how #443/#501/dogfooding relate to its explicit flip-to-replace conditions |
| [09-test-suite.md](09-test-suite.md) | Test-suite analysis | ~24k LOC / ~920 tests — pinned invariants, missed invariants, fragility signals, oracle strength |
| [10-issue-history.md](10-issue-history.md) | GitHub issue history & defect classification | ~35 defects since the v2 engine, classified: identity-model / unmodeled-scope / state / UX / ordinary-bug |
| [11-critique-salvage.md](11-critique-salvage.md) | Critique: the case for salvage | Strongest honest case for consolidating in place — what is verified-sound and what a rewrite would forfeit |
| [12-critique-rewrite.md](12-critique-rewrite.md) | Critique: the case for a principled replacement | Strongest honest case that the core abstractions are wrong — and the replacement model |
| [13-critique-abstraction.md](13-critique-abstraction.md) | Critique: neutral abstraction analysis | Mechanism-by-mechanism enumeration of the identity model; which bugs map to which seam; the systematic alternative |
| [14-critique-agent-ux.md](14-critique-agent-ux.md) | Critique: agent ergonomics | Why manual agent syncing beats the tool; the comprehension traps; the minimal agent contract |

Method stats: 14 agents, ~1.9M output tokens, 246 tool calls, ~12 minutes
wall-clock (parallel). Orchestrated as a two-phase workflow: a parallel read
fan-out, then four independent critiques over the pooled evidence.
