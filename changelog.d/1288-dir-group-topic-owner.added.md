- **Owning topics for global dir-groups** (#1288): `<dir-group topic="…">` and
  `<subdir topic="…">` name the topic(s) that own a global dir-group's files,
  so `clm release` ships per-topic code (completed solutions, starter kits)
  with its topic instead of freezing it into the skeleton. The value lists
  candidate topics; the first one in course order whose section is enabled
  owns the files, and with none enabled the subdir is not copied. An unknown
  topic id is a spec error; `clm validate` reports `invalid_dir_group_owner`
  and `disabled_dir_group_owner`.
