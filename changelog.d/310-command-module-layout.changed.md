- **Internal: command modules mirror the command tree.** Finding a command's
  definition is now mechanical: flat `clm <cmd>` lives in
  `commands/<cmd>.py`; `clm <group> <cmd>` lives in
  `commands/<group>/<cmd>.py` (package groups `slides/`, `course/`,
  `export/`) or `commands/<group>.py` (single-file groups, e.g. `db.py`,
  `git.py`, `calendar.py`). Groups register their own subcommands where
  they are defined; `main.py` is just the top-level manifest. No
  user-visible change, but `clm.cli.commands.*` import paths moved —
  external code importing them must follow the renames.
