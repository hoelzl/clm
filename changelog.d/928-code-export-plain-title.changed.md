- C++ code export (#928): the deck header macros (`header`, `header_de`,
  `header_en` in `templates_cpp/macros.j2`) emit a plain Markdown title
  (`# Title` plus the author line) for the code format instead of the HTML
  title slide, which showed up as a `<div>`/`<img>` comment block at the top
  of every exported `.cpp`. Notebook and HTML outputs are unchanged.
