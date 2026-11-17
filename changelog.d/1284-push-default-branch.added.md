- `clm git push`/`sync --channel` and `clm release sync --push` now point a
  channel's GitLab `default_branch` at the branch the push just created, for
  channels with `<share-with>` and when a GitLab token is configured (#1284).
  A new cohort's project no longer keeps advertising `main` after the first
  push of `master`, and no second `clm release provision` run is needed.
  Routine pushes make no API call; a refusal is a warning and never changes
  the exit code.
