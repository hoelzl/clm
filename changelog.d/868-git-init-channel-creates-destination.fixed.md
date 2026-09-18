- **`clm git init --channel` on a cohort that was never synced creates the
  destination instead of sending you to `clm build` (#868).** The channel
  destination is created by `clm release sync`, not by the build, and
  `release sync --push` in turn wanted the repo to exist — a cycle neither
  hint named. Init now creates the empty destination directory (dry-run says
  so) and initializes the repo in it, so `git init --channel` then
  `release sync --push` works from a cohort's very first delivery; `clm info
  releases` documents the bootstrap order. The related `default_branch`
  papercut is tracked as #955.
