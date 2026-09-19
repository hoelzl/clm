- **Model-free historical deck export (#960).** `harvest export-at-rev DECK
  --rev SHA -o NEW_DIRECTORY [--json]` exports the historical deck, split
  twin, and voiceover companions without processing recordings or changing
  working-copy files. Port/compare tasks now read companion narration, and
  comparison freshness includes companion content. Comparison artifacts and
  rendering are separated from the legacy embedded judge.
