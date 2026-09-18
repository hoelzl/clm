- **`clm release sync --refreeze-skeleton PATTERN` re-copies frozen skeleton
  files, and the first sync lists what it freezes (#869).** Skeleton files
  (setup docs, READMEs) were frozen by the first sync with no CLI escape
  hatch — `--refreeze` is topic-only and `<evergreen>` had to be declared up
  front. The new repeatable option is the topic `--refreeze` for the
  onboarding surface: a one-shot, stateless re-copy from the current build
  (labelled `refreeze-skeleton` in the plan) that also delivers a skeleton
  file the cohort never received. The first sync now prints the skeleton
  files it is about to freeze, so the decision is visible while it is cheap.
  `clm info releases` documents both, plus the two behaviours that follow
  from the stateless evergreen check (a pattern added later works; an absent
  evergreen file counts as differing).
