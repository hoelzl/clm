---
status: active
owner: maintainers
updated: 2026-11-20
review-by: 2027-05-20
---

# State: HTTP replay and the kernels of non-opted-in topics (#1309)

**S28.** The owner asked for an investigation of #1309 and a proposed
solution, then said "Please implement this". The agent fixed it end to end
(resolve-issue, then ship-a-pr). The conversation has two owner turns, so
almost all of the substance is the agent's proposal and the work after it.

Task state is in PR #1311 (merged 2026-11-20, closes #1309). There is no
handover and no design note; the reasoning is in the PR body, the commit
message and the transcript.

## Settled

- **Root cause.** A replay build exports the proxy variables to the whole
  worker process, so every kernel inherits them, but only topics with
  `http-replay` get the tag bootstrap. Untagged traffic from other topics
  then hits the catch-all cassette, the `CLM-HTTP-REPLAY-UNTAGGED` warning
  and, in strict modes, the `clm_replay_miss` 404.
- **Fix shape (proposed by the agent, accepted by "Please implement this").**
  Start the kernels of non-opted-in topics with the pre-proxy environment,
  recorded in `CLM_HTTP_REPLAY_PREPROXY_ENV` when the build exports the
  proxy, so a user's own corporate proxy survives. Docker: the recorded
  values are the proxy from `~/.docker/config.json`.
- **Rejected alternative:** tag every kernel as "passthrough" and let the
  addon forward untagged-but-marked traffic. It keeps all traffic going
  through the TLS-intercepting proxy for topics that never asked for it, and
  still needs every client stack patched.
- **Opt-in, not tag, decides.** A correction from the adversarial review: the
  first version kept the proxy only when a tag was injected, so an opted-in
  deck with a missing cassette in `replay` mode would have reached the live
  network with real keys. Now an unknown or opted-in mode keeps the proxy
  (fails closed).
- **Host env restored at build teardown,** so a second in-process build (MCP
  server, tests) does not inherit a dead proxy or a stale marker.
- **Notebook output epoch bumped to 5** — the kernel environment of
  non-opted-in decks changes, and nobody could argue their output cannot.

## Known weak points / residuals

- Docker mode only benefits after the worker images are rebuilt with a clm
  that contains #1311; an old image still sends these kernels through the
  proxy (documented in the user guide and the UNTAGGED warning).
- Subprocesses of an opted-in kernel that honour `HTTP(S)_PROXY` (pip, curl)
  are still untagged by design; the warning now names them.
- The course-side `TIKTOKEN_CACHE_DIR` / `FASTEMBED_CACHE_PATH` mitigation is
  no longer needed for this bug; it can stay as a cache optimisation.

## Next boundary

None open. Resume only if an UNTAGGED warning or strict-replay refusal shows
up again from a topic without `http-replay`, or when the Docker images are
next rebuilt (confirm the fix reaches containers).
