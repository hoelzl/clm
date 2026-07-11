- `clm slides sync apply`: a `body` answer on a single-line j2 macro member
  (e.g. the `id:title` header macro) no longer dead-ends with a spurious
  `# %%` delimiter rejection. The answer now replaces the macro line in
  place and accepts either the full j2 line
  (`# {{ header_de("Neuer Titel") }}`) or the bare replacement text, which
  is spliced into the macro's quoted argument (#609).
