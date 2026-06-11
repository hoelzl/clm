- `clm slides normalize` gained a `placeholder_start` operation (issue #233
  item 4a): a code cell tagged `start` whose body is only a solution
  placeholder (`# Your solution here`, `pass`, `...`) followed by a markdown
  `completed`/`alt` cell is demoted to a plain cell, and an already-promoted
  markdown `completed` partner is renamed back to `alt`. Placeholder `start`
  cells paired with a code `completed` cell are left untouched. Runs before
  `tag_migration` (which otherwise promotes the adjacent markdown `alt` to
  `completed`), is part of `all`, and is idempotent.
