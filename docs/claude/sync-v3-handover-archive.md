<!-- HANDOVER-ARCHIVE — fully retired on 2026-07-11 -->

# Handover Archive: Sync v3 Core Replacement (#520)

> ⚠️ **FULLY RETIRED HANDOVER — NOT ACTIVE**
>
> This document archives a handover whose work is fully complete or has
> been abandoned. **There is no active handover document.** It must
> **not** be used with `/resume-feature`, `/implement-next-phase`, or
> similar commands that expect an active work plan.
>
> If you need to resume related work, start a fresh handover.

---
Sync v3 core replacement (#520) — **COMPLETE through Phase 4 (cutover)**.

**State (2026-07-04)**: Phases 0–4 are done. The document-model engine is the
only sync engine: `clm slides sync {report,apply,verify,record}`, committed
per-topic ledger (`<topic>/.clm/sync-ledger.json`) as the sole trust store,
`report --since DATE|REF` as the bundle-at-ref forensic view. The v2 core
(`sync_plan`/`sync_apply`/`sync_code`/`sync_task`/`sync_accept`/…), the
watermark store (`SyncWatermarkCache` + friends), the sync-judge model
clients, `sync_autopilot`, `clm slides watermark`, and the `CLM_SYNC_ENGINE`
flag were deleted (PRs: #532 model+lenses, #534 differ+shadow, #537
apply+ledger+dispatch, cutover PR — see `git log` / #520).

**Where things live now**:
- Engine: `clm/slides/{bilingual_doc,doc_lenses,doc_identity,sync_diff,
  doc_ledger,doc_apply,doc_report,doc_write}.py`; CLI facade
  `clm/cli/commands/slides/sync_v3.py`; verb layer `…/slides/sync.py`.
- Shared git helpers (verify's HEAD read, --since's bundle-at-ref):
  `clm/slides/git_text.py`.
- MCP `slides_sync_report` and Studio `compute_lock`/sync-runner ride on
  `doc_report.diff_bundle`.
- `split`/`translate` seed the ledger on freshly-created pairs.

**Remaining / follow-ups** (file issues if picked up):
- Phase 5 options: MCP `sync_apply_decisions`, stable deck id, ledger
  analytics.
- The v2 evidence harnesses (`scripts/{edit_dynamics_harness,
  sync_corpus_harness,sync_matrix_probes}.py` + their test drivers) were
  deleted with the engine — their verdict catalogs encoded v2 auto-apply
  semantics. If an edit-dynamics-style fault-injection sweep is wanted for
  v3, it is a redesign over `report`/`apply --decisions` (frame-vs-silent
  classification), not a port.
- An optional human one-shot (`autopilot`-as-script over report→judge→apply)
  was NOT rebuilt at cutover — the agent loop is the supported path.
- Downstream: PythonCourses (and other course repos) agent guidelines must
  drop task/accept/watermark wording; seed ledgers once via
  `clm slides sync record slides/` from a verified state (see
  `clm info migration`).

**Read before touching the engine**: memory topic `sync-assessment-2`,
design `docs/claude/design/sync-total-identity-document-model.md`
(§5 ledger, §6 diff, §7 transitions, §8 surface), and the LANDMINES in the
memory topic (P8 frame-don't-mechanize, per-side id-keyed base orders,
DiffItem twin convention, pool-scoped confirms, verify gate at the verb
layer).
