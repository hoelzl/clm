- **A percent-encoded form parameter name is now filtered at record time
  (#881).** `api%5Fkey=SECRET` in an `application/x-www-form-urlencoded`
  request body was compared literally against the filter list and recorded
  verbatim, while the URL-query filter decoded the same spelling. Names are now
  read the way `parse_qsl` reads them — `%XX` and `+` decoded — by one shared
  reader that both the recorder and `clm cassette scan` use, so the audit
  reports these bodies and the two cannot drift. Unmatched fields keep their
  exact bytes. Such a body is part of the replay match key, so an affected
  cassette replay-misses loudly after upgrading; `clm info migration` has the
  note. No HTTP client CLM talks to encodes a parameter *name*, and the
  PythonCourses audit found none.
