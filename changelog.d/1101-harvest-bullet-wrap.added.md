- `clm harvest accept` now wraps the bullets it writes to the deck style: 80
  columns, continuation indented under the bullet text (`#   ` / `//   `),
  never breaking inside a backtick code span or a URL and never starting a
  continuation line with a markdown block marker. Only the cells the answer
  edits are rendered; other cells are never re-wrapped, so no post-pass
  rewrap script is needed. The `harvest-bullets` answer schema also accepts
  one level of nesting, `{"text": "…", "children": ["…", …]}`, rendered as
  `#   - …` / `//   - …` sub-bullets; deeper nesting and malformed entries
  are rejected with a precise reason. (#1101)
