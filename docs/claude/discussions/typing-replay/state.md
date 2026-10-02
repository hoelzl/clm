---
status: active
owner: maintainers
updated: 2026-10-02
review-by: 2027-04-02
---

# State: typing replay for recording notebooks

Mechanism, data contract, verification log, landmines and roadmap live in
`docs/claude/design/typing-replay.md`. The extension is
**hoelzl/jupyterlab-clm-typing** (public, MIT), checked out at
`~/Programming/Python/Projects/jupyterlab-clm-typing`. This file keeps only the
conversational position.

## Settled (S11, 2026-10-02)

- **Feasible, and verified with a live C++ kernel.** CodeMirror transactions
  without an `input.type` annotation avoid the auto-indent and bracket-closing
  problems that sank the clx attempt. Two real CppCourses decks were typed and
  run with no compile errors in the cam-cpp container (xcpp20), in both modes.
- **Both modes behind one global switch** for comparison (owner). Hacker mode
  was the agent's proposal, and the owner was enthusiastic: "leaves the human
  unpredictability in the loop but eliminates typos".
- **A line per step in V1.** Markers are optional, never required on every
  cell.
- **Sidecars, not inline annotations.** The owner proposed reusing slide ids
  and their sync tooling. Agreed with a refinement: the sidecar holds overrides
  only, keyed by slide id plus code-cell position, with a `(start, target)`
  fingerprint, one per deck.
- **A separate extension**, not a feature of the RISE fork.
- **Step 1 shape (owner, after the first save):** a new opt-in
  `recording-code-along` output kind, notebook only, with no notes,
  voiceover or `alt` cells. It is the notebook that is *recorded*; the
  `recording` notebook guides the narration.

## Shipped after the conversation (2026-10-02, autonomous within the owner's asks)

- The extension repo was made public (owner) and packaged with a committed
  prebuilt labextension, so it is pip-installable from git without Node.
- **#1023 merged** as PR #1027. An adversarial review found 9 issues, and 6
  were fixed, including two solution leaks: the docs claimed `speaker/`
  routing that never applies, and JupyterLite would have bundled the kind.
  #1026 was filed for a pre-existing bug: `engine.py`'s public-kind set
  misses `partial`.
- The cam-notebook images come from PythonCourses `docker/cam-notebooks`
  (owner pointer, confirmed). The extension is pinned there by commit
  (PythonCourses `4e09d7d`). All four 0.5.4 images were built and verified
  locally. **Not pushed to Docker Hub**: the agent asked, and there is no
  answer yet.

## Open / deferred

- **Push the 0.5.4 images** and move the `latest`/`cpp`/`polyglot`/`python-ml`
  tags? This is the owner's decision. The owner's running `cam-cpp`
  container still runs 0.5.2-cpp with a hand-installed extension.
- **Real recordings:** decide between step and hacker mode.
- **Edit order for start→completed cells** (attribute a → method b → …):
  the override sidecar, deferred past V1 by the owner.
- **Voiceover "trigger the next step" marker:** the owner's first step
  toward synchronized and eventually automated recordings. Not designed.
- **VS Code player** for Python: proposed, not discussed further.
- **#1026**, the public-kind set (separate, small).

## Weak points to keep in mind

- Slide navigation in RISE with an armed cell has not been tested.
- In hacker mode, keys are captured window-wide while a cell is armed. If
  focus moves elsewhere, keys are still swallowed until Esc.
- The extension's `scripts/merge_typing.py` pairs by output cell id, which is
  positional. It is a dev stand-in only; use clm's `recording-code-along`.

## Next boundary

The owner tries a real recording from a `recording-code-along` notebook
(listed on the private `speaker` target), using an image with the extension,
and reports which mode works.
