# Cassette secret filtering — handover

**Created**: 2026-08-18 | **Status**: #875, #878, #877 and #874 CLOSED;
**#883 in flight**, **#881 open** | **Next**: land #883, then #881 is the only
thing left in this arc

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
response-body shapes through **both** sides and asserts only that they agree;
#877 added a second table of ~45 **request** bodies (JSON and form-encoded,
`str` or raw `bytes`). **Add a row whenever you touch either side.**

Three properties of that suite, learned the hard way — preserve them:

1. **It asserts agreement, not outcomes.** Encode what either side *does* and it
   becomes a second copy of the thing it checks.
2. **Direction is pinned separately.** Parity alone is satisfied by both sides
   ignoring a case, so a few tests assert the result (the plaintext is gone from
   what the recorder writes).
3. **It does not normalise its inputs.** The first version passed
   `text.encode()` to one side and `text` to the other, so the byte-decoding
   limb went untested — and that is exactly where the BOM bug lived. The
   request table repeated the trap in a different shape: typed `str`-only, it
   *could not express* a non-UTF-8 body, and that is where the form-body
   false all-clear lived. Both tables now carry `bytes` and convert once.
4. **It cannot catch a shared blind spot** (the #877 lesson). Two sides that
   are consistently wrong *agree*, so the suite stays green. Nothing structural
   fixes that; the only defence is asking what the table does not cover — and
   the answer for a year was "request bodies". Note the precise history: the
   suite was green because it never fed a request body through *either* side,
   not because the sides agreed on one. Both are true, and only the first
   explains why nobody noticed.

## 2. Where the code is

| Thing | Location |
|---|---|
| Response filter, shared predicates | `src/clm/infrastructure/http_replay_mitm/cassette_format.py` |
| — value-type rule | `is_secret_body_value` |
| — repeated-name detection | `load_json_noting_duplicate_secrets` |
| — the walk | `_redact_json_values` |
| Request filter | `src/clm/infrastructure/http_replay_mitm/vcr_format.py` (`replace_post_data_parameters`) |
| — the shared request-body walk | `filter_json_parameters` (same module; the audit calls it too) |
| Audit / `clm cassette scan` | `src/clm/workers/notebook/cassette_doctor.py` (`scan_cassette_secrets`, `_iter_secret_body_keys`, `_json_or_none`) |
| — request JSON bodies | `_json_body_param_names` (delegates to the recorder's walk) |
| — request form bodies | `_form_body_keys` (hand-mirrors `_replace_form_parameters`) |
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
- **Numbers, booleans and null are exempt** from redaction — on the **response
  side only**. The tempting inverse rule — "redact only strings" — **leaks**:
  `{"secret":{"value":"sk-live-…"}}` gets recursed into and `value` is not on
  the key list. Do **not** copy the exemption to the request side: it exists
  because redaction rewrites what replayed code *reads*, and a request body is
  never handed back to the notebook. Adding it there would change the replay
  match key for every committed cassette carrying a numeric one, for no gain.
- **The info topics are load-bearing.** `clm info commands` lists the finding
  kinds *exhaustively*, and downstream course-repo agents read it. Adding a
  finding kind without updating it makes those agents wrong — this is CLAUDE.md's
  "Info Topics Maintenance Rule (CRITICAL)".

## 4. Open work

### #877 — request-body filter does not recurse (**CLOSED**)

`{"data": {"api_key": "sk-live-LEAK"}}` was recorded **verbatim** and
`clm cassette scan` reported the file **clean** — a false all-clear in the audit
#874 gates on. The response side recursed; the request side did not, and neither
did the scanner's request-body branch.

Why it was invisible, precisely: the parity suite had **no request-body rows at
all**, so neither side was ever exercised. And adding rows would not have caught
it either, because the two sides were *consistently* top-level-only and
therefore agreed. Both facts matter — the first explains why nobody noticed, the
second is the durable lesson (a parity test cannot catch a shared blind spot).

What landed:

- `vcr_format.filter_json_parameters` — one recursive walk, used by **both** the
  recorder and the audit. The request JSON side is now single-implementation,
  unlike the response side's two walks; the parity rows guard a future
  reimplementation rather than a live divergence.
- Recursion covers nested objects, arrays, and a top-level array root. A
  matched key takes its subtree with it (stop-at-match, like the response
  side). **No value-type exemption** on the request side — see the landmine
  below.
- Parse, walk *and* `json.dumps` under one guard on the JSON branch; the
  (CLM-unreachable) `dict`-body branch, which only walks, got the same
  `RecursionError` guard. An escaping `RecursionError` reads to the addon as
  "unfilterable" → live network in every mode, nothing recorded. The limb
  actually reproduced escaping is the new Python-level *walk*, at
  `setrecursionlimit(300)` / depth 400; on this box's CPython 3.13 the parse
  always blows before `json.dumps` would, so the dumps half is hardening
  rather than a demonstrated bug.
  **Know what the `dict` guard cannot do**: `build_request_filter` deep-copies
  the request first, so a *deeply nested* mapping body blows up in
  `copy.deepcopy` and never reaches it. What it does catch is a **cyclic**
  body — `deepcopy`'s memo resolves the cycle and the walk runs away. Both
  need a `dict` body, which only a hand-written cassette produces.
- The audit dispatches on body **type before content-type**, like the
  recorder: a YAML-mapping `body:` is walked whatever the header says. Falling
  through to the form reader (which rejects non-`bytes`/`str`) made such a
  file a false all-clear.
- **`_form_body_keys` now hand-mirrors `_replace_form_parameters` instead of
  using `parse_qsl`.** Three divergences, found in review:
  - a name with **no `=`** (bare `token`) is stripped by the recorder
    (`partition` yields an empty separator, not `None`) — was a false
    all-clear;
  - `parse_qsl` **percent-decodes** names and turns `+` into a space, the
    recorder does neither — those were findings no re-record could clear;
  - decoding the **whole body** as strict UTF-8 hid every body with a
    non-UTF-8 byte in a *value*, though the recorder only ever decodes names
    and does strip the secret next door. That was a false all-clear in the
    replay-miss class. An undecodable *name* still bails the whole body,
    because that is exactly what the recorder does.

  Verified by differential fuzz: 12 000 form bodies, 0 divergences.
- Docs: `clm info migration` gained a `#877` section (new replay-miss class,
  plus the form-reading change in both directions), `clm info commands` and
  `docs/user-guide/http-replay.md` updated.

Three things it left behind:

- **#881** — the recorder does not percent-decode a form parameter *name*, so
  `api%5Fkey=SECRET` records verbatim. The audit agreeing (not reporting it) is
  now correct under the contract, so this is a **recorder-side leak**, not a
  parity bug. Fixing it changes the form-encoded replay match key, hence its own
  issue. The shape is a passing row in the parity table with a note above it.
- The request-side parity predicate is **"the recorder removes a parameter"**,
  not "the bytes changed". A JSON *object* request body is re-dumped through
  `json.dumps` even when nothing matched, so byte inequality would call every
  JSON request body dirty.
- `scan_cassettes_for_secrets` still only guards `load_cassette` against a
  rogue exception; anything else escaping kills the whole repo walk.
  Unreachable today (payloads come from `json.loads`), left alone rather than
  wrapped in a blanket `except` that would mask real bugs.

### #874 — course-repo cassette audit

Unblocked by #876. **Already measured, do not re-run to learn this** (2026-08-18,
PythonCourses `slides/`):

| | before #876 | after #876 | after #877 |
|---|---|---|---|
| cassettes | 204 (all git-tracked) | 204 | 204 |
| dirty files | 97 | **95** | **95** |
| findings | 302 | **294** | **294** |
| by location | 294 response header + 8 response body | 294 response header | 294 response header |

The #877 column is the important one: the widened recursive walk finds
**nothing new**, so no PythonCourses cassette will replay-miss because of it.

- The 8 that disappeared were the `encoder.json` false positives (#875).
- **No credentials.** Enumerating the `Set-Cookie` *names* across all 204 files
  (the scan records only the header name, so this needs its own pass) gives
  exactly eight, none of them a credential: `__cf_bm` ×270 (Cloudflare
  bot-management, ~30 min TTL), `WMF-Uniq` ×24, `WMF-Last-Access` ×18,
  `WMF-Last-Access-Global` ×18, `GeoIP` ×18, `NetworkProbeLimit` ×18, `WMF-DP`
  ×10 (all Wikimedia analytics/geo), `grokipedia-affinity` ×2 (routing).
  These counts are **cookies**, and sum to 378; the 294 in the table are
  **findings**, one per `set-cookie` *header*, and a single header can carry
  several cookies. Different metrics, same corpus, same day — an earlier
  version of this section counted cookies but summed them against the finding
  total, which reconciled only by coincidence.
- **Zero request-side findings**, confirmed again *after* #877 — decisive,
  because those are the only class in the replay match key. Nothing will start
  failing to replay, so re-recording is byte-hygiene and can ride along with
  other work rather than being a batch job.
- **CppCourses and CSharpCourses have no cassettes at all.** PythonCourses is
  the entire scope. Within it the dirty files are `module_550_ml_azav` (84 of
  its 104), `module_545_ml_azav_cohort_2026_04` (9) and
  `module_555_ml_azav_deep_dive` (2).

Re-run with:

```bash
cd <PythonCourses>/slides && clm cassette scan --json
```

**#877 has landed and the scan was re-run against it** — same numbers, so a
green request-side is now evidence rather than an artefact of a top-level-only
walk.

**Decided (2026-08-18), issue CLOSED**: re-recording is **opportunistic** — a
flagged deck gets re-recorded when it is next touched for other reasons, which
is this issue's own suggestion. A batch re-record was rejected: 262 of the 294
findings are `openrouter.ai`, i.e. nondeterministic LLM responses, so
re-recording rewrites what *replays* and therefore the rendered output of 84
decks of live AZAV teaching material — course churn to remove a Cloudflare
token with a ~30-minute TTL, plus a fresh chance at the chain-orphan failure
mode `clm cassette doctor` exists for. Nothing is waiting on it: zero
request-side findings means no replay miss.

The issue's last item — wire the scan into a repo check — was blocked on
`clm cassette scan` exiting 1 on all 294 benign findings, so the gate could
never be green. Split out as **#883** (a baseline of accepted findings) and
implemented.

### #883 — accepted-findings baseline (**in flight**)

`clm cassette scan --write-baseline PATH` / `--baseline PATH`. Without them
nothing changes; a bare scan still fails on any finding.

Dogfooded on PythonCourses: 294 findings → **95 baseline entries** (one per
file — the key has no index, so a deck's repeated cookie is one entry), gate
exits 0; injecting a nested `api_key` into one request body makes it exit 1
with exactly that one new finding.

The design is all in the match key — **`(path relative to the scan root,
location, key)`** — and the two omissions are the point:

- **No interaction index.** Re-recording shifts every index, so an index-keyed
  baseline would fail the gate the first time somebody did the thing the audit
  asks for.
- **No value.** A finding never carries one (the report must not print
  secrets), and `__cf_bm` rotates on every recording.

**The cost is name-level acceptance**: blessing `deck / response header /
set-cookie` blesses *any* `set-cookie` in that file, including a real session
credential. Inherent — the audit only ever sees the header name — and pinned by
`test_a_different_cookie_in_a_baselined_file_is_accepted` so it stays a
decision rather than a surprise. A new finding *kind* in a baselined file is
still reported.

Other load-bearing choices: stale entries (a deck that *was* re-recorded) are
reported, never fatal, or the gate would punish the fix; an unreadable cassette
is not baselineable and still fails, which is why `--write-baseline` exits
non-zero after writing when it meets one; a malformed baseline raises rather
than degrading to accept-nothing (unsatisfiable) or accept-everything (a false
all-clear); keys are stored lowercased and paths POSIX **on both the read and
the write side**, because the repo holds both `set-cookie` and `Set-Cookie`, a
Windows-written baseline has to match on Linux CI, and normalising one side
only makes the round trip asymmetric.

**Two review Criticals, and they are the same shape — read this before
touching the gate.** Both were the gate going green, or looking green, over
something nobody had checked.

1. *Round 1*: a baselined run over a tree with **no cassettes** exited 0. The
   signal was there — every entry unmatched, none accepted — and it simply was
   not acted on. Wrong CI working directory, content that did not materialise,
   a renamed root: all green over a repo nothing looked at.
2. *Round 2*: the fix for (1) refused **before rendering the report**. So a
   repo that had re-recorded its baselined decks *and* grown a new secret was
   told its scan root was wrong, never shown the secret — and the message's own
   advice (`--write-baseline`) would then have blessed it. A guided false
   all-clear, strictly worse than the silent one it replaced.

So: **report first, refuse second**, and never suggest regenerating while
`outcome.new` is non-empty.

The check itself keys on **missing files**, not on "nothing matched".
`stale_cleared` (file scanned, finding gone) is a deck that was re-recorded —
the audit's request carried out — and must stay green, or the gate punishes its
own fix. `stale_missing` (file never scanned) is a sparse checkout, moved decks,
or the wrong root, and fails. Keying on "nothing matched" conflated the two and
turned a fully-remediated repo red.

Also of note: an early `return` on "no cassettes found" was blamed for (1) in
an earlier draft of this document. It was not the cause — removing it changes
nothing, since an empty tree has no findings either way.
`describes_another_tree` is the whole fix.

The hole this does **not** close is a **partially** symlinked tree —
`iter_cassette_paths` does not follow directory symlinks, so those cassettes are
silently unscanned. Pre-existing, tracked as **#886**, and newly worth fixing
because this arc turns the command into a gate. Those entries now at least
surface as `stale_missing`.

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
