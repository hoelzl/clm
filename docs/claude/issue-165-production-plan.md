# Issue #165 — production plan (mitmproxy HTTP-replay transport)

Phase 0 is complete and all three GO gates are GREEN (see
`issue-165-phase0-findings.md`): CA trust, deadlock-class elimination, and
single-proxy throughput at 16-worker load. The architectural risk is retired.
What follows is **known production engineering**, not unknowns.

**Invariant for every phase below:** vcrpy stays the **default**;
`CLM_HTTP_REPLAY_TRANSPORT=mitmproxy` is opt-in and must remain bit-identical to
today when unset. vcrpy is **never uninstalled** — it remains the pure YAML
serializer behind `clm cassette doctor`, `strip_cassette_hosts.py`, and
`merge_staging_into_canonical`. The default flips to mitmproxy only after every
parity gate holds across a full release cycle, and the 8 in-kernel workarounds
are deleted only then.

Current state (branch `claude/issue-165-mitmproxy-transport`, draft #173, stacked
on insurance #172): `MitmproxyManager` + `ClmReplayAddon` (native `.mitm`
format), opt-in Direct-mode wiring, 4 smoke + 3 no-op regression tests.

---

## P1 — vcrpy-YAML cassette bridge  ·  ~3–4 d  ·  risk: medium  ·  **DONE**

**Status (done):** the addon now persists the vcrpy v1 YAML schema via a
pure `cassette_format.py` bridge (imports only `vcr` + stdlib; importable
both as a CLM submodule and by bare path inside the `uv tool` mitmdump
interpreter, which now carries vcrpy via `uv tool install mitmproxy --with
vcrpy`). The bridge routes through vcrpy's own `serialize` / `Request` /
`decode_response`, so output is byte-identical to a vcrpy-recorded
cassette. Gate proven by `tests/infrastructure/test_http_replay_mitm_cassette_format.py`
(byte-identity vs vcrpy for plain + gzip + multi-value headers, LF
endings, no `convert_to_unicode` aliasing leak, round-trip load, and
`merge_staging_into_canonical` folding a bridge cassette). The 4 mitmproxy
smoke tests pass against the new format (record → replay → strict-miss
599). Still single-shared-cassette — per-target routing is P2.

The addon persisted native `.mitm` before. Production must read/write the
**existing vcrpy v1 YAML schema** so committed course cassettes and the
doctor/strip/merge tooling keep working unchanged.

- Convert `HTTPFlow` ⇄ vcrpy `(Request, response-dict)` at the addon boundary.
- Write via `vcr.serialize.serialize` + `FilesystemPersister` — verified
  near-pure (import `yaml` / `vcr.request.Request` / `vcr.serializers.compat`;
  no live VCR/cassette/patching context needed). Read via `vcr.deserialize`.
- **Must:** LF-only writes through the shared atomic-write helper (not
  `FilesystemPersister`'s default newline) to avoid CRLF flapping under the
  repo's `eol=lf` gitattributes; emit headers as dict-of-lists and
  `response.body.string` as UTF-8 text so `cassette doctor` / `strip` load it;
  `decode_compressed_response` parity (gzip/deflate/br); avoid the
  `convert_to_unicode` bytes→str in-place mutation (the workaround-#2 footgun)
  by not aliasing dicts between the index and the writer.
- **Gate:** a mitmproxy-recorded cassette for a deck is byte-comparable to the
  vcrpy-produced one; `cassette doctor` and `strip_cassette_hosts.py` operate on
  it unchanged.

## P2 — request→cassette routing (concurrent multi-topic)  ·  ~2–3 d  ·  risk: medium  ·  **DONE**

**Status (done):** each worker injects a lightweight tag bootstrap (patch
httpx `Client`/`AsyncClient.send` to add `X-CLM-Cassette: <canonical>`)
instead of the vcrpy bootstrap under the transport; the tag is
`payload.http_replay_cassette_name` resolved exactly like the vcrpy path,
so it **already carries the #159 split-deck base-cassette fallback** (replay
→ fallback-aware name, record → strict name) and equals the host
merge-discovery canonical by construction. The single addon demuxes flows
by tag into one `<cassette>.staging-mitm-<build_id>` per canonical (vcrpy
YAML), strips the tag before recording/forwarding, and routes untagged
traffic to a catch-all so strict replay never escapes. The **host** writes
the `.completed` marker for this build's staging in the build `finally`
(`Course.merge_mitmproxy_cassette_staging(build_id)`) — the reliable
build-completion signal (mitmproxy's `done` hook does not fire on a Windows
`CTRL_BREAK`) — then folds via the existing `merge_staging_into_canonical`.
A force-killed build never reaches the marker step, so its staging stays
markerless and the next pre-build sweep discards it (issue #115 semantics,
at build-end granularity). Gate proven: a routing smoke test (two tagged
+ one untagged request → correct per-cassette staging, tag stripped, no
cross-contamination, catch-all, real host marker+merge round-trip) plus
unit tests for tag injection/strip, tag resolution, and the host merge
(folds markered, leaves markerless, LF endings).

**Adversarial review (workflow `wf_eeeba6cb-563`, 5 probe-driven lenses)
confirmed 4 findings; the convert_to_unicode aliasing concern was refuted by
a control probe (the `serialize_interactions` deepcopy is load-bearing).**
Fixed in this branch: (1) **HIGH** — `once` mode aborted the build because the
Phase-0 catch-all existence guard in `addon.running()` checked the
always-empty build-scratch catch-all; removed it, `once`/`refresh` now route
per-target via the mode sets (regression test added). (2) **LOW** — a
non-ASCII HTTP/1.1 reason phrase was recorded where vcrpy would crash and the
result was not vcrpy-replayable; `cassette_format` now drops it to `None`
(ASCII reasons stay byte-identical). **Deferred** (not P1/P2 regressions): the
`refresh`-overwrite gap (the mode-blind `merge_staging_into_canonical` keeps
the stale canonical entry — *identical behavior in the in-process vcrpy path*,
so a cross-cutting merge change → P3) and the catch-all's O(n²) eager
full-rewrite (latent; tagged per-notebook targets stay tiny, proven fine at
the 18-notebook / 506 KB scale → perf follow-up).

The Phase-0 proof used **one shared cassette**; production needs each request
mapped to the correct per-(topic, language, kind) cassette under concurrency.

- Tag each request per worker (e.g. an `X-CLM-Cassette` header injected by the
  worker env / a tiny client hook, stripped before recording) so the single
  addon demuxes flows into the correct per-target staging file — or run one
  proxy per worker (rejected unless P1/throughput says otherwise; the shared
  proxy is proven sufficient).
- Reuse the existing `merge_staging_into_canonical` (resolve_paths, FileLock,
  `.completed` markers, `sweep_orphans`, dedup key) — format-agnostic, hardened
  by the #145 + BytesIO fixes.
- Preserve the `.de`/`.en` split-deck base-cassette fallback (#159/#161).
- **Gate:** a concurrent DE/EN multi-topic build routes each topic's flows to the
  correct staging file and merges with no cross-contamination; split fallback
  still resolves.

## P3 — correctness/security parity in the addon  ·  ~3–5 d  ·  risk: high

The prototype addon lacks parity with the vcrpy bootstrap's load-bearing
behavior (the design doc wrongly called these "trivial").

- **Strict `once` / `refresh` semantics per target** (deferred from P2's review):
  under the transport `once` currently behaves like `new-episodes` and `refresh`
  records additively. `refresh`-overwrite needs a **mode-aware merge** —
  `merge_staging_into_canonical` is mode-blind and keeps the stale canonical
  entry on a re-record (the *in-process vcrpy path has the identical bug*), so
  the fix (thread the mode through; staging-wins for `refresh`/`record`) lands
  here and benefits **both** transports. `once`-must-exist should error/miss per
  tagged canonical rather than via the build-scratch catch-all.
- **Secret filtering** at record time: `authorization`, `cookie`, `x-api-key`,
  `set-cookie` headers + post-data/query params (match the bootstrap's
  `filter_headers` / `filter_post_data_parameters` / `filter_query_parameters`).
- **`ignore_hosts`** pass-through (default `api.smith.langchain.com`,
  `CLM_HTTP_REPLAY_IGNORE_HOSTS` override) so LangSmith telemetry never enters
  the cassette (re-introducing the PR #146 churn).
- **JSON-semantic body matching** in `_request_key` (parse JSON when
  content-type is `application/json` case-insensitively; byte-compare otherwise)
  — matching `_clm_json_body_matcher`, or a byte-exact key 599-misses real LLM
  JSON POSTs.
- **Strict-replay-miss → loud build failure**: verify the 599 propagates as a
  non-zero build exit equivalent to vcrpy's `CannotOverwriteExistingCassetteException`
  + the #93 exit policy, and is not swallowed/retried by the langchain/openai
  retry layers.
- **Kill-survival:** confirm the addon flushes each flow synchronously on
  response (eager-append equivalent) so a build-timeout kill of mitmdump loses
  no recorded interactions.
- **Manager robustness:** drain mitmdump stdout on a reader thread (avoid a
  multi-hour-build PIPE-buffer deadlock); define proxy lifetime under watch-mode
  rebuilds (one proxy across the watch session vs per-rebuild).
- **Gates:** no-op rebuild yields an empty cassette git-diff and contains no
  secrets/LangSmith; real LLM JSON POSTs replay-hit (no spurious 599); a
  deliberate miss fails the build non-zero.

## P4 — Docker worker support  ·  ~3–5 d  ·  risk: high  ·  separately gated

A `127.0.0.1` mitmdump is unreachable inside a container.

- Rewrite the container env host to `host.docker.internal` (mirror the
  `CLM_API_URL` pattern; `extra_hosts: host-gateway` is already set); add
  `HTTP(S)_PROXY` + `SSL_CERT_FILE` to the Docker env allowlist; mount + trust
  the per-build CA inside the container.
- **Gate:** a Docker-worker build completes an HTTPS LLM round-trip via
  `host.docker.internal` with CA trust. **If infeasible:** Docker stays
  vcrpy-only and vcrpy is retained for it — this does not block Direct-mode
  mitmproxy, but vcrpy deletion is gated on Docker being supported or explicitly
  scoped out.

## P5 — trace-harness re-port + vcrpy retirement  ·  ~3–5 d  ·  risk: medium

- Re-port the Phase-A diagnostic: its `socket.connect` ground-truth survives,
  but the vcr-stream events go dark — rebuild the methodology against proxy logs.
- Run the full parity-gate suite (below). If all pass and Docker is supported or
  scoped out, flip the default to mitmproxy for one release with
  `CLM_HTTP_REPLAY_TRANSPORT=vcrpy` still selectable as rollback insurance.
- **Only then** delete the 8 in-kernel workarounds + the ~480-line bootstrap
  templates. Keep the Option-F per-cell timeout as a transport-agnostic
  loud-failure net. Keep vcrpy installed as the YAML serializer.

---

## Parity gates (gate vcrpy default-flip / workaround deletion)

1. Deadlock-defeat: 16-worker stress repro, 0 stalls, py-spy-clean (✅ Phase 0).
2. HTTPS round-trip on Windows against real endpoints (✅ Phase 0, at scale).
3. Cassette byte-identity vs vcrpy (compressed bodies decoded, dict-of-lists
   headers, UTF-8 body string, LF endings) — P1.
4. Tooling: `cassette doctor` / `strip_cassette_hosts` / merge load mitmproxy
   cassettes unchanged — P1.
5. Secret/telemetry hygiene: no auth/cookie/api-key headers, no LangSmith; no-op
   rebuild grows nothing — P3.
6. JSON-match parity: real LLM JSON POSTs replay-hit, no spurious 599 — P3.
7. Strict-replay failure parity: a miss → non-zero build exit, not swallowed — P3.
8. Concurrency routing: DE/EN multi-topic routes correctly, no contamination;
   split fallback resolves — P2.
9. Default-unchanged: transport unset ⇒ build bit-identical to vcrpy (✅ tests).

## Sequencing & estimate

P1 → P2 → P3 are the core (vcrpy-parity); they can partly overlap but P3 depends
on P1's format. P4 is independent and optional-for-v1. P5 is the cutover.
**Core (P1–P3): ~8–12 d. With P4–P5: ~14–22 d.** Each phase ships behind the
flag with its parity gate; nothing changes the default until P5.

## Open design decisions for the owner

- **Routing mechanism (P2):** per-request worker tagging vs per-worker proxies.
  Tagging keeps the proven single-proxy model and one cassette-merge path;
  per-worker proxies re-open port management. Recommendation: tagging.
- **Docker in v1 (P4):** support now, or ship Direct-only and keep vcrpy for
  Docker? The recordings/CI footprint determines urgency.
- **Upstream-first:** if the upstream vcrpy `close()` + scoped `force_reset`
  patches (`docs/claude/vcrpy-upstream-patches.md`) land, the #143/#129 forks
  retire independently — reducing the urgency delta but not the cross-process
  capability story.
