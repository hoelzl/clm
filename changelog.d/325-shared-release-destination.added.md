- **Shared release destinations.** Channels of *different* release streams may
  now declare the same `path`, releasing e.g. materials and solutions into a
  single cohort repository on independent per-topic timelines (#325). Frozen
  manifests are now per stream (`.clm-released.<stream>.json`; a matching
  legacy `.clm-released.json` is adopted and renamed automatically on the next
  sync), skeleton files already present at the destination are kept rather
  than overwritten (presence-as-frozen), `clm release sync` refuses to promote
  when the sharing streams' builds claim a topic-owned path with differing
  content (byte-identical static files, e.g. project scaffolding, are
  allowed), spec validation
  requires sharing channels to agree on `lang`, and `clm git --all-channels` /
  `clm release provision` treat a shared destination as one repository. See
  `clm info releases` ("Shared destination") and `clm info migration`.
