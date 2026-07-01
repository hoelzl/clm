- **Recordings dashboard: split decks no longer show both languages at once.**
  The lecture list enumerated every notebook in a section, so a split deck
  (`slides_foo.de.py` + `slides_foo.en.py`) appeared as two rows regardless of
  the selected language. The DE/EN toggle now filters split companions by their
  `output_language_filter`, so it actually switches between languages and a
  split deck contributes a single row.
- **Recordings dashboard: same-named DE/EN decks are controlled independently.**
  When a split deck's German and English titles coincided, both rows shared a
  `deck_name` and reacted to a single Record/Arm/Stop control (recording one
  language appeared to record the other). The row-level armed match now also
  compares the recording language, so DE and EN versions of a deck can be
  recorded independently.
- **Recordings dashboard: section cards are collapsible.** Each section is now a
  `<details>` block whose open/closed state is remembered across live updates,
  so a long lecture list can be collapsed down instead of scrolling.
