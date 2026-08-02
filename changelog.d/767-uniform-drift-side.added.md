- **`clm slides sync report`** now emits a deck-level `uniform_drift_side`
  observation when three or more `translate_edit` items exist and *every* one
  drifted on the same language half — the shape a review-after-translate pass
  leaves. Each row names its own side, but says nothing about the
  others, so a report full of them still reads as N separate members to work
  through; the observation is the one line saying they are N views of a
  single event. It carries `side`, so a driver can branch on it without parsing
  prose, and it reports rather than recommends: the engine sees which side
  *moved*, never which side is *authoritative*, so both readings (bank the
  reviewed half with `keep_twin`, or supply adapted bodies for the twin) are
  spelled out. Members needing two-sided verification are counted separately so
  a blanket sweep cannot pick up rows that do not accept `keep_twin`. Appears in
  the JSON envelope and in the text report, below the items it summarizes.
- **`clm slides sync report`**: `translate_edit` item details now name the
  `keep_twin` answer alongside "adapt the twin".
