- **The shared-cell German-text finding is now an `error`.** The #772
  detector was born a `warning` while the corpus carried pre-existing German
  shared cells; the cleanup finished at 0 findings across all 659 PythonCourses
  split pairs, so the code/j2 boundary is categorical again and new German in a
  shared (no-`lang`) code cell fails `clm validate` instead of advising. The
  per-cell `allow-untranslated` tag remains the escape hatch for cells where
  German is the point (a DE↔EN dictionary example, regex lessons over German
  data). This retires the #771 base-rate caveat on `record_neutral`'s
  `NEUTRAL_KINDS`: unmarked German in a shared code cell can no longer
  survive a `clm validate` gate unnoticed, so banked `shared` trust stops
  accumulating it silently (banking itself stays ungated — `sync record`
  runs only the structural verify, which deliberately never sees this
  content heuristic).
  The check is also the first declared exemption to the gate⊇validate
  containment property: a validate error the sync write gate deliberately
  never sees, because the halves agree byte-for-byte (structurally valid
  trust) and content-language heuristics belong to the advisory oracle, not
  the trust oracle. A repo that still carries findings either translates the
  flagged cells or tags them — see `clm info migration`. (#782)
