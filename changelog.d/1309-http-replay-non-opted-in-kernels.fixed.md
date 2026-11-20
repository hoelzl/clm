- **HTTP replay: kernels of topics without `http-replay` no longer use the replay proxy** (#1309).
  A replay build exported the proxy (and its CA bundle) to every worker, but only opted-in
  topics got the cassette-routing tag, so other decks' traffic reached the proxy untagged:
  recorded into the machine-local catch-all, flagged with a misleading
  `CLM-HTTP-REPLAY-UNTAGGED` warning, and refused with a `clm_replay_miss` 404 in strict
  modes (`replay`, `once`). Their kernels now start with the proxy variables restored to
  their pre-build values, in Direct and Docker mode. The untagged-flow warning now names
  the remaining causes (an untagged client library, a subprocess, an outdated worker).
