- **`clm validate` flags German text in shared code cells.** A shared
  (no-`lang`) code cell is emitted verbatim into both language outputs, so
  `# Das ist ein Kommentar.` in one leaks German into the English deck — and
  once banked as `shared` trust, a later one-sided fix frames the mechanical
  `propagate_shared_edit` overwrite (#771's review reproduced that end to
  end). The new cross-file `pairing` check scans comments and string literals
  only (identifiers/keywords are English by construction; corpus measurement:
  German in 0.92% of shared code cells vs 7.5% English — an 8× asymmetry), and
  warns once per split pair on the `.de.py` side. Intentional German — the
  DE↔EN dictionary example — opts out per cell via the new validate-only
  `allow-untranslated` tag; because shared cells are byte-identical across the
  halves, the hatch can never be applied one-sidedly. English text is
  deliberately not flagged (legitimate cases: docstrings, string-lesson demo
  strings). Warning severity, matching the split-pair family: pre-existing
  committed German must not hard-fail CI; `--fail-on warning` gates it where
  wanted. (#772)
