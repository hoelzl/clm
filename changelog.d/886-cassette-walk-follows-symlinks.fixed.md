- **`clm cassette scan` and `clm cassette doctor` follow symlinked
  directories (#886).** The walk used `Path.rglob`, which does not recurse
  into a directory link, so a cassette behind one was invisible to both
  commands — and since the scan became a CI gate (#883) that was a green run
  over a tree nobody looked at. The walk is now `os.walk(followlinks=True)`
  with loop protection (a link cycle terminates; a directory reachable by two
  routes is walked once), identical on every supported Python.
