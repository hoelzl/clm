---
status: active
owner: maintainers
updated: 2026-10-03
review-by: 2027-04-03
---

# State: typing replay for recording notebooks

Mechanism, data contract, verification log, landmines and roadmap live in
`docs/claude/design/typing-replay.md`. The extension is
**hoelzl/jupyterlab-clm-typing** (public, MIT), checked out at
`~/Programming/Python/Projects/jupyterlab-clm-typing`. Its `vscode/`
directory holds the VS Code player (S12). This file keeps only the
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
  (PythonCourses `4e09d7d`). All four 0.5.4 images, with extension 0.1.0,
  were built and verified; another agent pushed them, and the owner moved
  `cam-cpp` to 0.5.4 (now port 8892).
- **Extension 0.1.1** (`3cac2b0`, owner request): Ctrl+Enter and Alt+Enter
  also leave hacker mode, like Shift+Enter. Verified with real keys in
  `cam-cpp` and installed there by hand.
- **On-screen cheat sheet** (owner request): private artifact
  https://claude.ai/artifact/9gUrMUj3EMgj3uPVqwFHUG. It covers the replay keys,
  hacker mode, running cells, the RISE fork's slideshow keys, and a four-step
  recording flow.
- **The 0.1.1 image update was handed off.** The agent found that a pin change
  in `base` re-uploads gigabytes. It drafted a last-layer `TYPING_UPDATE_REF`
  install, measured at about 160 kB per push, and built 0.5.5-cpp/polyglot
  locally. The owner then said another agent is releasing 0.5.6, which
  rebuilds the lower layers anyway and takes over the Dockerfile commit. 0.5.5
  was never pushed, and its local tags were removed. The edit is left
  uncommitted in the PythonCourses checkout.

## Settled (S12, 2026-10-02/03): the VS Code player

- **A separate extension, in the same repo** (agent's proposal, owner: "Your
  proposed layout is fine"). It is not part of jupyter-slide-nav, which stays
  a generic Marketplace extension built on RISE/nbconvert metadata. It lives
  in `vscode/` and imports the shared `src/planner.ts`/`player.ts`, so the
  two players can't drift apart. An npm package for the shared code waits for
  a third consumer.
- **The owner doesn't use VSCodeVim**, so the conflict over the `type`
  command doesn't apply.
- **Alt+N always enters edit mode** (owner bug report → 0.1.1). In command
  mode after Shift+Enter, Alt+N used to leave the cell "half-armed": Enter
  typed the script, but `A` inserted a cell.
- **Command-mode keys while armed re-enter edit mode and type** (0.1.2). The
  agent offered this, laid out its consequences when the owner asked, and the
  owner accepted: "It sounds good." The accepted cost is that single-key
  notebook shortcuts need a disarm first. The more conservative variant (any
  command-mode key disarms and is dropped) was rejected: it changes the
  symptom without removing it. The owner verified the fix by hand; the German
  layout wasn't tested, but the bindings use physical key codes.
- **CI plus GitHub releases, not the Marketplace** (owner request). The
  `.vsix` ships as GitHub releases `vscode-v<version>` from a manual
  workflow. A Marketplace listing only makes sense if others should use it.
- **Recording profile:** the owner is setting one up with inline suggestions
  (Copilot ghost text) turned off.

## Shipped after the conversation (S12, within the owner's asks)

- hoelzl/jupyterlab-clm-typing PR #1 (player plus the 0.1.1 fix; the owner
  merged it), #2 (command-mode keys, 0.1.2) and #3 (CI and the release
  workflow) are merged. Release `vscode-v0.1.2` was cut by the new workflow.
- **clm 1.31.0 released** (owner request; PR #1030, PyPI and GitHub Release)
  with #1023.
- The design doc's §6 item 5 records the player as done (clm PR #1032,
  merged).

## Open / deferred

- **0.5.6 must ship extension 0.1.1.** The other agent owns it: fold
  `TYPING_UPDATE_REF` into `TYPING_REF` = `3cac2b0`, or keep the last-layer
  slot for future updates. Check the 0.5.6 images afterwards.
- **Real recordings:** decide between step and hacker mode.
- **Edit order for start→completed cells** (attribute a → method b → …):
  the override sidecar, deferred past V1 by the owner.
- **Voiceover "trigger the next step" marker:** the owner's first step
  toward synchronized and eventually automated recordings. Not designed.
- **#1026**, the public-kind set (separate, small).

## Weak points to keep in mind

- Slide navigation in RISE with an armed cell has not been tested.
- In hacker mode, keys are captured window-wide while a cell is armed. If
  focus moves elsewhere, keys are still swallowed until Esc.
- The extension's `scripts/merge_typing.py` pairs by output cell id, which is
  positional. It is a dev stand-in only; use clm's `recording-code-along`.
- VS Code: no test presses real keys. The keybinding paths (armed keys in
  edit mode, command-mode keys, Esc) were checked only by the owner's manual
  run and a unit test of `package.json`. The German layout wasn't checked by
  hand.
- VS Code: slide-nav's spacer cells and Toggle Slide View, combined with an
  armed cell, are untested.

## Next boundary

The owner tries real recordings from a `recording-code-along` notebook
(listed on the private `speaker` target): C++ in JupyterLab with the cheat
sheet on screen, and Python in VS Code with the new recording profile. The
owner reports which mode works and which start→completed cells were typed in
an awkward order. Those cells are the input for the override sidecar
(step 3).
