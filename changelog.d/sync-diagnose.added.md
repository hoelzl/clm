- **New `clm slides sync diagnose` verb — classify a `verify` symptom into its root
  cause.** A `verify` failure (`id-asymmetry`, `duplicate-id`) has several unrelated
  root causes, each needing a different fix; renaming ids until `verify` passes hides
  the real defect. `diagnose` is a read-only superset of `verify` **and**
  `reconcile-vo-ids`: for every finding — plus the verify-invisible narrative
  id-disagreements (a narration cell id'd on one half, id-less on the other) — it
  names the root cause (`MIS-TAG` / `ID-LESS-TWIN` / `CONTENT-GAP` / `WHOLE-DECK-GAP`
  / `DUPLICATE-NARRATION-OVERSTAMP` / `NARRATIVE-ID-DISAGREEMENT` / …), the evidence
  (content-language vs `lang=` tag, who carries the id, whether a twin exists), and
  whether the fix is mechanical or authoring. `--apply` performs **only** the
  identity-preserving narrative fixes (strip a duplicated/asymmetric narration id to
  the canonical id-less form), re-gated by structure; it **never** renames an id to
  silence a symptom. Content language is judged by a tiny built-in DE/EN heuristic
  that abstains on short/title-only text. Read-only by default, `--json` for agents;
  the root-cause catalog ships in `clm info sync-agents`. Also adds
  `reconcile_vo_ids.collapse_intra_half_duplicates` for the symmetric narration
  over-stamp the existing reconciler left alone.
