- Fixed a build crash (`shutil.Error` / WinError 32 sharing violation on
  Windows) in the final dir-group copy: for explicit output targets whose
  kinds span both public and speaker (e.g. `{code-along, trainer}`), the
  public/speaker split collapses to a single output path, but two copy
  operations were still launched and raced `shutil.copytree` against each
  other on the same destination files. Dir-group copy operations are now
  deduplicated by (destination, sources) across the whole build, which also
  covers dir-groups repeated in a course spec.
