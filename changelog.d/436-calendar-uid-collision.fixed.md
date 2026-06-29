- **Cohort calendar: fix UID collisions that silently dropped events (#436).**
  `clm calendar generate -f ics` and `clm calendar push` seeded each event's
  stable UID from the bare slide-file **stem**, which is unique only within a
  topic — so two distinct decks that share a stem (a common pattern: many topics
  name their lead deck `slides_010_*`) produced the **same** UID and collided.
  One event was silently dropped from the `.ics` feed and from a pushed Google
  calendar (`duplicate event UID … keeping the later assignment`). The UID is now
  seeded from each deck's globally-unique **`module/topic/stem`** identity, which
  eliminates the entire collision class. **One-time migration:** because every
  video/merged event's UID changes (date-keyed review/exam inserts keep theirs),
  the first `clm calendar push` after upgrading re-creates those events once
  (read-only `.ics`/Google subscribers see a one-time refresh — no emails, since
  the events have no attendees, and no manual step). Preview it with
  `clm calendar push … --dry-run`. See `clm info migration`.
