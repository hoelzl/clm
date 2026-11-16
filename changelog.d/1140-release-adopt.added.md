- `clm release adopt SPEC --channel NAME (TOPIC_IDS | --section SEL | --all-present)`
  migrates a course that published by pushing an output target's repo to release
  channels: it freezes the topics the cohort repository already holds, without
  copying. Each file is classified `same` / `differs` / `missing` against the
  current build; an adopted topic's digest comes from the destination's bytes,
  `source_commit` is `published:<dest HEAD>`, and differing topics carry
  `differs_from_build`. Topics with missing files are refused unless
  `--deliver-missing` (copies only the missing files, never overwrites). The
  skeleton is marked frozen without copying. Adopt removes the `origin` remote of
  output-target working trees that point at the channel's origin and prints the
  restore command (`--keep-output-remote` keeps it); `--dry-run` / `--json`
  preview the plan. In output-target mode, `clm git init` / `push` / `sync` now
  refuse a remote that is a release channel's origin (`--allow-channel-remote`
  overrides). Procedure in `clm info releases` (#1140).
