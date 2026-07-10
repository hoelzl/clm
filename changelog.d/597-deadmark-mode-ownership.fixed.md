- The activation-timeout dead-marking in the SQLite backend is now scoped by
  execution mode and session ownership (#597). Previously, a Direct-mode
  build timing out on its own workers could mark a concurrent Docker-mode
  build's still-starting pre-registrations (`status='created'`, over 30s old
  — plausible for a cold Docker image pull) as dead in a shared jobs DB,
  making that build fail with "No workers available" through no fault of its
  own. The UPDATE now applies the same mode discriminator as the surrounding
  availability queries and only dead-marks workers the build's own lifecycle
  session registered (unowned legacy rows keep the issue-#348 fail-fast).
