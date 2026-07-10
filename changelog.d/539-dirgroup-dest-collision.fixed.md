- Dir-group copy deduplication is now per destination directory instead of
  per dir-group, so two `<dir-group>`s that overlap in a single `<subdir>` no
  longer copy it twice concurrently (the racing `copytree` calls aborted
  otherwise-complete Windows builds with WinError 32 at the final dir-group
  stage). `clm validate` now warns (`duplicate_dir_group_destination`) when
  several dir-groups resolve to the same output destination — both for
  redundant duplicates and for conflicting sources (#539).
