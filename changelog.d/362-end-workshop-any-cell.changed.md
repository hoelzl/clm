- The `end-workshop` tag is now honored on **any** cell type, not just
  markdown ([#362](https://github.com/hoelzl/clm/issues/362)): many workshops
  end with a code cell (the final solution or assertion), and tagging that
  cell previously was a silent no-op that `clm validate` (correctly) warned
  about. Both workshop range scanners (notebook build and slide tooling) now
  close a range at an `end-workshop` cell of any type; workshop *openers*
  remain markdown-only. The close stays **exclusive** — the tagged cell is
  outside the workshop, so tagging the workshop's final code cell excludes
  that cell from the range (it renders completed instead of blanked; for a
  `keep`-tagged assertion cell the output is identical either way). The
  validator accepts `end-workshop` on code cells and extends the
  orphan-`end-workshop` warning to them.
