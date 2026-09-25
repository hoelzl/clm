- `clm slides sync report` frames the deck header member (`id:title`, the
  `{{ header_de(...) }}` / `{{ header_en(...) }}` j2 line) with its macro
  line in `de_body` / `en_body` (#993) — they were empty, since a single-line
  j2 cell has no body below its delimiter, and agents had to guess that the
  answer is the full macro line. The excerpt is now valid decision input for
  header rows too (feed it back edited as the `body`, or answer the bare
  title text). The second half of the report — an applied header body that
  stayed framed on the next report — was the deck-wide structural gate of
  #992 (fixed in 1.29.0, PR #995); a header body banks in the same apply,
  now pinned by a regression test. Applies to the MCP `slides_sync_report`
  tool as well.
