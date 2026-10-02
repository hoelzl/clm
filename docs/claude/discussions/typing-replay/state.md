---
status: active
owner: maintainers
updated: 2026-10-02
review-by: 2027-04-02
---

# State: typing replay for recording notebooks

The design and the verified spike results are in
`docs/claude/design/typing-replay.md`. The spike itself is a standalone
JupyterLab 4 extension at `~/Programming/Python/Projects/jupyterlab-clm-typing`
(GitHub: hoelzl/jupyterlab-clm-typing, private). This file keeps only
the conversational position.

## Settled (S11, 2026-10-02)

- **Feasible.** Editor transactions without an `input.type` annotation
  avoid the auto-indent and bracket-closing problems that sank the clx attempt.
  This was verified in the browser in C++ editor mode and in the RISE fork's
  slideshow, but without a live kernel.
- **Both modes behind one global switch** for comparison. The owner expects
  to settle on one after real recordings. Hacker mode was the agent's
  proposal, and the owner was enthusiastic: "leaves the human unpredictability
  in the loop but eliminates typos".
- **A line per step in V1.** Markers are optional, never required on every
  cell.
- **Sidecars, not inline annotations.** The owner proposed reusing slide ids
  and their sync/rename tooling, and the agent agreed with one refinement:
  the sidecar holds overrides only, keyed by slide id plus code-cell position,
  with a `(start, target)` fingerprint for stale detection, one per deck rather
  than per language.
- **A separate extension**, not a feature of the RISE fork.

## Open / deferred

- **Edit order for start→completed cells** (attribute a → method b →
  attribute c → method d): the main use case for markers. It needs the
  override sidecar and was deferred past V1 by the owner.
- **Voiceover "trigger the next step" marker:** the owner's proposed first
  step toward synchronized narration and eventually automated recordings.
  Not designed yet.
- **Mode choice:** waits on real recordings.
- **Docker image:** the extension needs pip packaging and installation into
  the image, then a test with a live xeus-cpp kernel. Unverified.
- **VS Code player** for Python: proposed, not discussed further.

## Weak points to keep in mind

- The spike never ran against a live kernel. Running a cell after typing
  wasn't exercised, though nothing in the extension depends on the kernel.
- The test notebooks had no slide metadata, so the RISE check covered a
  single long slide. Navigating between slides with an armed cell wasn't
  tested.
- In hacker mode the key capture is window-wide while a cell is armed. If
  focus moves elsewhere, keys are still swallowed until Esc.

## Next boundary

Step 1 is #1023. Filing it corrected a premise of the conversation: the
`recording` kind is completed + notes + voiceover, not code-along, and the
public `code-along` kind must not carry solutions. The owner then decided
(2026-10-02, after the S11 transcript boundary): a new opt-in
**`recording-code-along`** output kind, notebook only, with no notes,
voiceover or `alt` cells. It is the notebook that is recorded; the
`recording` notebook guides the narration. The issue body holds the decisions
and acceptance criteria.

The spike extension now lives at **hoelzl/jupyterlab-clm-typing** (private
GitHub repo, `master`). The next conversation is either the #1023
implementation, or packaging plus a dogfood recording on a real C++ deck.
