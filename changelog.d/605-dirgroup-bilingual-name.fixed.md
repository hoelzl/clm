- Bilingual `<dir-group>` names (`<name><de>…</de><en>…</en></name>`) are now
  honored instead of silently parsing as empty — previously such a group's
  content was copied to the root of every output target (#605). All bilingual
  spec elements (course/section names, `<description>`, `<certificate>`,
  `<organization>`, dir-group names) now share one bilingual-or-simple parser:
  plain text applies to both languages, a single `<de>`/`<en>` child falls
  back to the other language, and unknown child elements or text mixed with
  `<de>`/`<en>` children raise a spec error instead of being dropped.
