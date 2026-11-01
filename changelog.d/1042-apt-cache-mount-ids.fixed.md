- Docker worker images no longer serialize on each other's `apt-get` steps: every
  apt BuildKit cache mount now has an explicit per-image `id=` (notebook lite and
  full get separate ids, since their Debian/Ubuntu package state differs). Without
  an id, all images shared one `sharing=locked` cache keyed on the target path, so
  a concurrent build of another image could stall an apt step for the whole
  duration of the other build's install (#1042).
