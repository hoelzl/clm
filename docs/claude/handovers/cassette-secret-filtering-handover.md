# Cassette secret filtering — handover

**Created**: 2026-08-18 | **Status**: #875 and #878 CLOSED (PR #876, merge
`e76acd59`); **#877 and #874 OPEN** | **Next**: #877, then #874

Companion to `adversarial-review-remediation-handover.md` (Phase 4 item 6,
which is where this arc started as finding S9). That document is the plan;
this one is the working state of the cassette follow-ups.

---

## 1. The one thing to internalise first

The record-time secret filter and the committed-cassette audit are **two
implementations of one contract**:

> The audit flags a body **iff** re-recording would rewrite it.

- Break it one way — a finding the recorder would not act on — and `clm cassette
  scan`'s exit-1 gate becomes **unsatisfiable**. Nobody can clear it, so it gets
  switched off.
- Break it the other way — a rewrite the audit misses — and it is a **false
  all-clear**, which is worse, because the whole point is to vouch for files.

Since PR #876 that contract is executable:
`tests/infrastructure/test_cassette_scanner_recorder_parity.py` pushes ~60
payload shapes through **both** sides and asserts only that they agree. **Add a
row whenever you touch either walk.**

Three properties of that suite, learned the hard way — preserve them:

1. **It asserts agreement, not outcomes.** Encode what either side *does* and it
   becomes a second copy of the thing it checks.
2. **Direction is pinned separately.** Parity alone is satisfied by both sides
   ignoring a case, so a few tests assert the result (the plaintext is gone from
   what the recorder writes).
3. **It does not normalise its inputs.** The first version passed
   `text.encode()` to one side and `text` to the other, so the byte-decoding
   limb went untested — and that is exactly where the BOM bug lived.

## 2. Where the code is

| Thing | Location |
|---|---|
| Response filter, shared predicates | `src/clm/infrastructure/http_replay_mitm/cassette_format.py` |
| — value-type rule | `is_secret_body_value` |
| — repeated-name detection | `load_json_noting_duplicate_secrets` |
| — the walk | `_redact_json_values` |
| Request filter (**#877 lives here**) | `src/clm/infrastructure/http_replay_mitm/vcr_format.py:472-540` |
| Audit / `clm cassette scan` | `src/clm/workers/notebook/cassette_doctor.py` (`scan_cassette_secrets`, `_iter_secret_body_keys`, `_json_or_none`) |
| CLI | `src/clm/cli/commands/cassette.py` |
| Parity suite | `tests/infrastructure/test_cassette_scanner_recorder_parity.py` |
| Recorder tests | `tests/infrastructure/test_http_replay_secret_scrubbing.py` |
| Audit tests | `tests/workers/test_cassette_secret_scan.py` |

`cassette_format` and `vcr_format` are **pure** (PyYAML + stdlib, no `clm`
imports) because `mitmdump` imports them by path in an interpreter where `clm`
is not installed. Do not add a `clm` import to either.

## 3. Landmines

- **A filter that raises is far worse than one that does nothing.**
  `_filter_request` returning `None` means "unfilterable", and the addon handles
  that **exactly like an ignore-host** — the request goes to the **live
  network** in every mode including strict `replay`, and nothing is recorded.
  One warning line, no miss. This is why every guard in this code prefers
  "leave it alone" to raising.
- **A dropped response is not a loud miss.** `_select_serve_index` repeats the
  last match, so a dropped response to a *repeated* request replays the previous
  one — silently different output.
- **Responses are not in the replay match key; request query and body are.**
  So response-side changes can never cause a replay miss, and request-side ones
  can. This is the single most useful fact for triaging scan findings, and it is
  what makes #877 delicate.
- **Guard the parse and the traversal together.** Which of the two overflows on
  a deeply nested body depends on the interpreter build — depth 1200 bails on
  Windows and Linux 3.13 but *completes* on Linux 3.12. Splitting the guard
  makes behaviour platform-dependent. Verify both regimes locally with
  `sys.setrecursionlimit(...)`; do not push to find out.
- **Redaction stops at a match.** An object or array under a secret-named key is
  replaced **wholesale by the placeholder string**, so replayed
  `x["secret"]["id"]` raises. Deliberate: redacting only the strings inside
  would miss a secret under a key that is not on the list. Documented in
  `clm info commands`, `clm info migration` and `docs/user-guide/http-replay.md`.
- **Numbers, booleans and null are exempt** from redaction. The tempting inverse
  rule — "redact only strings" — **leaks**: `{"secret":{"value":"sk-live-…"}}`
  gets recursed into and `value` is not on the key list.
- **The info topics are load-bearing.** `clm info commands` lists the finding
  kinds *exhaustively*, and downstream course-repo agents read it. Adding a
  finding kind without updating it makes those agents wrong — this is CLAUDE.md's
  "Info Topics Maintenance Rule (CRITICAL)".

## 4. Open work

### #877 — request-body filter does not recurse (**do this first**)

`{"data": {"api_key": "sk-live-LEAK"}}` is recorded **verbatim**, and
`clm cassette scan` reports the file **clean**. The response side recurses; the
request side does not, and neither does the scanner's request-body branch.

Why it is invisible: the two sides are *consistently* top-level-only, so parity
holds and the new suite passes. A shared blind spot, not a divergence — which is
the failure mode a parity test structurally cannot catch, and worth remembering
before trusting that suite too much.

Why it matters: this is a **false all-clear** in the audit that #874 gates on.

**The care it needs**: request bodies **are** part of the replay match key, and
record and lookup filter through the same code. Making the filter recurse
changes both what a new cassette contains *and* what an incoming request is
normalised to before matching — so existing cassettes carrying a nested secret
will start **missing on replay**. That is the class `clm info migration` already
documents for S9; extend that note, and make sure `clm cassette scan` reports
the affected entries so they are findable.

Acceptance: nested `api_key`/`password`/`token` stripped at any depth; the audit
reports them; the parity table grows **request-body rows** (it has none today —
which is why this slipped past it); migration note updated.

### #874 — course-repo cassette audit

Unblocked by #876. **Already measured, do not re-run to learn this** (2026-08-18,
PythonCourses `slides/`):

| | before #876 | after #876 |
|---|---|---|
| cassettes | 204 (all git-tracked) | 204 |
| dirty files | 97 | **95** |
| findings | 302 | **294** |
| by location | 294 response header + 8 response body | 294 response header |

- The 8 that disappeared were the `encoder.json` false positives (#875).
- **No credentials.** The only recorded cookies across all 204 files are
  `__cf_bm` (Cloudflare bot-management, ~30 min TTL) ×268, `WMF-Last-Access`
  ×18, `WMF-Uniq` ×6, `grokipedia-affinity` ×2.
- **Zero request-side findings** — decisive, because those are the only class in
  the replay match key. Nothing will start failing to replay, so re-recording is
  byte-hygiene and can ride along with other work rather than being a batch job.
- **CppCourses and CSharpCourses have no cassettes at all.** PythonCourses is
  the entire scope.

Re-run with:

```bash
cd <PythonCourses>/slides && clm cassette scan --json
```

Land **#877 first** if you intend to treat a green scan as evidence.

## 5. Test-suite flakiness on the Windows dev box (read before panicking)

A red local suite is **not** evidence your change is broken. Measured over 17
full `pytest -m "not docker"` runs on 2026-08-18:

| tree | runs | with a failure |
|---|---|---|
| feature branch | 9 | 4 |
| `origin/master` | 8 | 1 |

Five distinct tests were involved across four unrelated subsystems
(`tests/release/test_release_cli.py::TestSyncPush`,
`test_sqlite_backend.py::test_stall_detector_progress_resets_the_clock`,
`tests/e2e/test_e2e_course_conversion.py`, and
`tests/infrastructure/workers/test_worker_base.py` — the last is the flake
`docs/claude/test-flakiness-investigation.md` names explicitly). All pass in
isolation. Fisher's exact on 4/9 vs 1/8 gives p ≈ 0.29 — not a real difference.
Tracked in **#847**, with the full A/B in its comments.

Practical rules: a failed **pre-push hook** is often this — re-run before
investigating. Suspect the test when the failure is a *different* test each run,
in a subsystem your diff does not import, and green in isolation. Get ≥5 master
runs before believing a local red means anything. **No cassette test flaked in
any run.**

## 6. Related documents

- `docs/claude/handovers/adversarial-review-remediation-handover.md` — Phase 4
  item 6 (S9) is where this arc began; the contract blocks there are settled.
- `docs/developer-guide/testing.md` — "Two implementations of one contract need
  a parity test" and "Assert the contract, not the platform's implementation
  limits", both written from this arc.
- `docs/user-guide/http-replay.md` — the user-facing filter rules.
- `docs/user-guide/troubleshooting.md` — "Cassette Issues": how to triage a scan
  report.
- `clm info commands` / `clm info migration` — version-accurate, and what
  downstream course-repo agents read.
