- Fixed a crash (`ValueError: invalid literal for int()`) in `clm git sync`
  (and `clm release sync --push`) when the remote-ahead check ran against
  empty `git rev-list` output — most visibly under `--dry-run`, where the
  mocked git call returns no stdout. The behind-remote count now treats empty
  output as "not behind" instead of parsing it as an integer.
