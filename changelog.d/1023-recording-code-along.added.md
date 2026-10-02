- **New opt-in `recording-code-along` output kind for typing replay (#1023).**
  It is the notebook a trainer records on video. It has exactly the
  `code-along` cells (no `notes`, `voiceover` or `alt` cells), plus
  `metadata.clm.typing = {"start": ..., "target": ...}` on every code cell
  whose completed source differs: answer cells (empty start) and `start`
  cells paired by tag with their `completed` cell. The
  [jupyterlab-clm-typing](https://github.com/hoelzl/jupyterlab-clm-typing)
  extension replays these edits as simulated typing, either step by step or
  by turning each key press into the next few script characters. The kind is
  notebook only, and built only when a target lists it in `<kinds>` (a target
  listing it must include the `notebook` format). It is never bundled into a
  JupyterLite site, and `--speaker-only` keeps it where a target opts in.
  Because its metadata holds the solutions, list it only on a private target
  such as `speaker`.
  Private-kind routing (image copies, root directories, provenance) now reads
  a single `PRIVATE_KINDS` set instead of four duplicated literals.
