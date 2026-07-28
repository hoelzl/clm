- `clm slides sync apply`: the verbatim-copy executor primitive
  (`copy_new_shared`, and the `treat_as_new` / `keep` answers) now mints the
  target half's `lang=` variant for a lang-attributed source cell instead of
  copying the source attribute verbatim (#717) — a wrong-language cell in the
  twin file would make the re-parse gate abort the entire apply pass.
