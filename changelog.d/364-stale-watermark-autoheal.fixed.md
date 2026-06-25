- **`clm slides sync apply` / `autopilot` auto-heal a stale watermark.** When both
  halves of a split deck were edited and committed without an intervening sync, the
  watermark falls behind and a watermark-baselined run errored on a *false*
  stale-baseline conflict (the "id-less localized cells edited on both decks" error,
  or a keyed conflict) even though the halves are already mutually consistent. A
  **writing** run now detects that case and re-baselines the watermark automatically
  before reconciling, so the sync just proceeds (#364). On by default and safe by
  construction — it heals only when git `HEAD` shows the halves consistent (a
  verified no-op), so it can never mask an un-synced edit, and never fires outside a
  git repo. Pass `--no-auto-heal` to surface the conflict instead; the `apply
  --json` payload carries `auto_healed: true` when it fires.
- **Stale-watermark messaging now points at the live verbs.** The stale-watermark
  hint and the id-less-localized error steered to the retired `clm slides sync
  --rebaseline` flag; they now point at `clm slides sync apply` (which auto-heals)
  and `clm slides sync baseline bless` (#364). The id-less error already names the
  offending cell's owning slide group and echoes the drifted cells.
