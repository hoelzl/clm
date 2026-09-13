- C++ code export (#928): section functions carry a `slide_` prefix
  (`void slide_brace_initialization()`, `slide_03` without a `slide_id`) so
  they are told apart from the deck's own functions; a new optional
  `section_name="…"` cell attribute overrides the `slide_id`-derived name
  (stripped from every output like `slide_id`). HTML in markdown cells is
  approximated as Markdown in the `//` comments (`<img>`, `<b>`, `<tt>`,
  lists, tables, `<div>` wrappers).
