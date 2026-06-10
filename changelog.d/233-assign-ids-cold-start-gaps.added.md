- `clm slides assign-ids` automates three more cold-start fixes (#233):
  display expressions (`data[:5]`, `result["choices"]`,
  `response.headers["Content-Type"]`) and `for` loops are now
  content-derived AST extractions instead of hard refusals; an alt-less
  `<img src="…">` proposes a slug from the image filename stem
  (`img-robots-playing-checkers`) instead of hard-refusing (multi-line
  `<img>` tags no longer leak attribute fragments into prose extraction);
  and voiceover/notes cells carrying `<deck-stem>-cell-N` conversion
  placeholder ids are re-pointed to the preceding slide on the normal
  inherit pass, without `--force`.
