- **Recordings dashboard: sections start collapsed and remember state across
  restarts.** The lecture list now renders every section collapsed by default,
  so it opens as a compact list of section headers instead of a long scroll.
  A section's open/closed state is now stored in `localStorage` (was
  `sessionStorage`), so a user's choices survive not just a page refresh but a
  browser restart and a `clm recordings serve` restart.
