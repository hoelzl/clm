---
status: active
owner: maintainers
updated: 2026-11-10
review-by: 2027-05-10
---

# State: studio preview review (#704, and what its root causes led to)

Saved retroactively on 2026-11-10 from the Hermes session of 2026-07-26 (S21).
Everything it decided shipped the same day, so this thread is a record, not
open work. The mechanisms live in the code and in
`docs/claude/design/mobile-deck-studio.md`; the standing rule it produced is
"The claimed-wired rule" in `docs/developer-guide/testing.md`, pointed at from
`AGENTS.md`. This file keeps only the conversational position.

## The owner's question (S21)

- An adversarial review of PR #704 (the Studio tier-2 preview, sanitizing macro
  HTML server-side), with a wider brief: "Since we have already seen two rounds
  of adversarial review discover critical bugs, investigate whether there are
  any major design mistakes, either in the design of #704, or other features
  that cause these problems, and if so, how we can address their root causes
  rather than putting patches on patches."

## Settled (S21, 2026-07-26)

- **The agent's answer: no new bypass in #704, and three systemic causes** for
  the two bad review rounds. (1) No defense in depth: the Studio page held a
  non-expiring bearer token in `localStorage` and the app sent no
  Content-Security-Policy, so any sanitizer miss was token theft. (2) Every
  confirmed bug lived in the hand-rolled policy layered over nh3, so that layer
  should shrink. (3) "Wired" claims and security docstrings without a test that
  executes them.
- **Merge #704, file the follow-ups, and make the pattern a standing rule**
  (owner): "in particular the promotion of the pattern into a standing rule."
  The agent implemented the rule directly instead of filing it (PR #707).
- **#706, option B: serve only the bundled logos** (owner, after asking what
  the choice affects: "The slides delivered to students? Or just the studio
  assets?"). Only the Studio's phone preview is affected; student output keeps
  its embedded `data:` logo. A course author's own `data:` image in a custom
  macro drops out of the preview, documented as "preview, not parity". Option A
  was a content-addressed asset store for any `data:` image. The agent
  recommended against it: it adds a store and an active-SVG question to an
  issue whose point is deleting hand-rolled security surface.
- **Take a break until the performance evaluation is done** (owner, at the
  end). That evaluation is thread `cache-performance-regression` (S22).

## Shipped (all merged 2026-07-26)

- PR #704: the tier-2 preview itself.
- PR #707: the claimed-wired rule (`docs/developer-guide/testing.md`,
  `AGENTS.md`).
- #705 in PR #708: Content-Security-Policy and security headers from
  `clm serve`. Two review rounds, seven minor findings, all fixed.
- #706 in PR #709: the logo as a same-origin asset, `data:` out of the
  sanitizer and out of the CSP. The review found a quadratic regex (ReDoS).
- #694 in PR #710: the failing nightly. A test reloaded the config singleton
  under a monkeypatched `CLM_LOGGING__LOG_LEVEL=ERROR`; a later in-process
  `clm build` applied it to the `clm` logger; some 300 tests later a `caplog`
  assertion on a WARNING failed. Fixed by an autouse fixture that restores the
  logger chain and the config singleton after every test.

## Open / revisit conditions

- None from this session. Two items were deliberately left: the token's
  lifecycle (short-lived pairing plus refresh), recorded as deferred in the
  Studio design doc, and a single mitmproxy connection flake in #710's CI run,
  not filed because it passed on rerun. Neither was re-checked at this save.

## Not in the transcript

- The subagent review reports and the background-process notices: the harness
  delivered them as user rows, and they were removed. Their findings are in the
  agent's own summaries and in the PRs.
- The body of the `next-issue` skill the owner invoked, reduced to one line.
- The copies of earlier rows that each context compaction re-inserted.

## Agent corrections recorded in the transcript

- An early probe looked like a UNC-path bypass of the sanitizer. It was Git
  Bash mangling backslashes in a heredoc; rebuilding the URLs from character
  codes showed the filter refusing all four spellings.
- The first "linear" fix for the ReDoS was still quadratic. The agent caught it
  by measuring again before committing; the final fix is a `str.find` loop that
  never rewinds.
- Removing `data:` from the CSP's `img-src` would have broken the pairing QR,
  which `qr.py` could serve as a `data:` URI. Checked: no served page used it.
- The nightly flake did not reproduce in eleven local full-suite runs (32
  workers on Windows against CI's 4 on Linux). What worked: instrument the real
  nightly workflow on a throwaway branch with a per-test snapshot of the logger
  state, which named the polluting test. The lesson, in the agent's words:
  reproduce the state corruption, not the flake.
- The reviewer for #710 died on a provider quota limit mid-review. The agent
  said so and substituted a documented self-review; it did not report the
  review as done.
