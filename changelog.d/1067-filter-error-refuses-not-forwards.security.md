- HTTP replay: a request whose secret filter raises is no longer forwarded to
  the live network. The mitmproxy addon used to treat a filter exception like
  an `ignore_hosts` match and pass the request through in every mode — strict
  `--http-replay=replay` included, so a crashing filter became a live,
  possibly billed call. It now refuses the request in **every** mode with the
  non-retryable `clm_replay_miss` 404, whose message names the filter error;
  recording modes fail closed too rather than forward a request they could
  not scrub. `ignore_hosts` hosts still pass through unrecorded. (#1067)
