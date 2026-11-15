- The narrative-cell writers behind `clm slides polish accept`, `clm voiceover sync`
  and `clm harvest autopilot` (`update_narrative` and the companion writer) no longer
  turn every line into its own bullet: wrapped continuation lines and indented child
  bullets are kept, and every bullet is wrapped to the deck style through the same
  helper `clm harvest accept` uses, so re-writing an unchanged cell is byte-identical.
  `update_narrative` also keeps the blank separator after a replaced cell (#1162).
