# Typing replay for recording notebooks

**Status**: SPIKE VERIFIED 2026-10-02; step-1 decisions settled. Nothing is
implemented in clm yet; step 1 is issue #1023 (see §3, §6). | **Created**: 2026-10-02
**Spike**: standalone JupyterLab 4 extension, GitHub
hoelzl/jupyterlab-clm-typing (private), checked out locally at
`~/Programming/Python/Projects/jupyterlab-clm-typing`. Its README has the run
instructions and the verification log.
**Related**: `docs/claude/discussions/typing-replay/state.md` (the argument),
the RISE fork (`~/Programming/Python/Projects/JupyterLabRise`, submodule
`rise/` → github.com/hoelzl/rise).

---

## 1. Problem

The trainer live-codes the empty and `start` cells of code-along notebooks
while recording and has to type and speak at the same time. This is hardest in
C++ notebooks (xeus-cpp in the Docker image), which have almost no usable
completion. The goal is to replay the code-along → completed edit of a cell as
simulated typing, in **recording** notebooks only.

An earlier clx attempt failed for two structural reasons that this design
avoids:

1. **It sent OS-level key events.** These go through the editor's input
   machinery (auto-indent, bracket auto-close, smart backspace, completion
   popups), which the driver had to predict and couldn't.
2. **It worked out the edits at playback time from a mathematical diff.**
   That gives the shortest edit, not the one a person would make, and there is
   no point at which to check the plan before recording.

## 2. Mechanism (verified)

- **Editor transactions, not key events.** The extension dispatches
  CodeMirror 6 transactions into the cell's `EditorView` *without* an
  `input.type` user-event annotation. `indentOnInput` (a transaction filter
  that only acts on `input.type`/`input.complete`) and `closeBrackets` (a DOM
  input handler) therefore never fire. The cell always ends with exactly the
  target text, and the indentation it shows is the indentation the plan types.
- **Plan from `(start, target)`, checked by replay.** An LCS line diff is
  processed hunk by hunk:
  - Paired lines are edited in place: the differing middle is selected, shown
    briefly, deleted and retyped. `/* TODO */` → `6 * 7` only touches the
    placeholder.
  - Surplus deleted lines are removed as one selected block.
  - Surplus inserted lines are typed one step per line. Each step starts with
    the newline ("press Enter, then type"), so the cursor rests at the end of
    the line just typed, and the newline and its indentation appear as one
    keystroke, which reads as auto-indent.
  - Blank lines fold into the next step, and an empty start is typed fresh.

  The plan is replayed in memory and compared with the target. If they differ,
  it falls back to "select all and retype". In 2000 random fuzz cases every
  plan reproduced the target exactly, and fewer than 2% needed the fallback.
- **One player, two modes**, switched by one global setting:
  - **step**: the hotkey types the next line at a jittered speed. Pressing it
    again mid-line finishes the line at once.
  - **hacker**: the hotkey arms the cell. Each plain key press then types the
    next 1–3 script characters, and the real keys are swallowed. After the
    script ends the cell stays armed and keeps swallowing keys, so over-typing
    can't leak characters into it. Shift+Enter (run), Esc or the hotkey
    disarms it. The spike originally disarmed itself at the end, and the
    over-typed characters landed in the cell.
- **Controls:** finish the cell, undo a step (restores the snapshot taken
  before the step), reset the cell.

### Verification scope

The spike ran in JupyterLab 4.5.7 with the RISE fork 0.43.1, using
`JupyterLabRise/envs/.venv-new`. The test notebook had `xcpp17` metadata, so
the editor ran in CodeMirror's `cpp` language mode, but **no kernel was
running** (Docker Desktop was down). Checked in the browser:

- exact final text with C++ braces and nesting;
- a placeholder replaced in place, and a class growing two methods and an
  attribute;
- hacker mode with real browser key events (CDP), including over-typing past
  the end;
- the RISE slideshow.

The RISE fork runs the slideshow as a separate app in an iframe. That app's
page config listed the extension, and typing worked there.

**Not yet verified:** a live xeus-cpp kernel in the Docker image, installing
the extension into that image, and real recording ergonomics (which mode
reads better on video).

## 3. Data contract (proposed for clm step 1)

**Which notebook carries it — decided (owner, 2026-10-02, #1023).** A new
private output kind, working name **`recording-code-along`**, notebook format
only, opt-in via the course spec. While recording, the trainer uses two
notebooks: the existing `recording` notebook (completed + `notes` +
`voiceover`) guides the narration, and the `recording-code-along` notebook is
the one that is recorded and shown to students. So it must look exactly like
the public `code-along` notebook: no `notes`, `voiceover` or `alt` cells.
The only difference is the typing metadata.

Why not an existing kind: `RecordingOutput` (`src/clm/workers/notebook/
output_spec.py`) deletes `start` cells, and its executed HTML is the cache
source for Trainer/Completed/Partial HTML. The `code-along` kind is public, so
typing targets in it would ship the solutions to students. The S11
conversation first assumed "the recording notebooks" already were
code-along; this was found while filing #1023.

That notebook writes, on each code cell whose completed source differs from
its code-along source:

```json
"metadata": {"clm": {"typing": {"start": "<code-along source>",
                                "target": "<completed source>"}}}
```

The cell's source stays the code-along source. Other output kinds never
carry the key. A `start` cell and the `completed` cell that follows it merge
into one cell (start source, completed target). The player computes the plan at load time, so clm doesn't
emit plans in V1.

## 4. Decisions (owner, S11)

- **Both modes** stay available behind one global switch for comparison. The
  owner expects to settle on one after real recordings, so there is no
  per-cell mode control.
- **Granularity:** a line per step is the V1 unit. Markers are optional, never
  required on every cell; annotating each cell would be too much churn.
- **Ordering of start→completed edits** (e.g. attribute a, then method b, then
  attribute c, then method d) is the main use case for markers. It is deferred
  past V1; line-by-line replay comes first.
- **Slides stay focused on the material.** Optional features live in
  sidecars, not inline annotations.
- **The sidecar holds overrides only.** The automatic plan is recomputed from
  the current sources, so most cells need no stored state.
  - Overrides are keyed by slide id plus the code cell's position within the
    slide, and carry a fingerprint of `(start, target)`.
  - A fingerprint mismatch makes the override stale. `clm validate` reports
    it, and the automatic plan takes over, so nothing fails silently.
  - Code cells are usually shared between de and en, so there is one typing
    sidecar per deck, not one per language. That is simpler to keep in sync
    than voiceover companions.
  - Reusing the slide-id rename/sync tooling for these keys was the owner's
    proposal, and the agent agreed.
- **Future narration link:** a voiceover marker telling the speaker to
  trigger the next step now, expandable later into automatic sync between
  typing and narration (and eventually fully automated recordings).
- **Where it lives:** a separate JupyterLab extension rather than a feature
  of the RISE fork. It works in notebook view and in the slideshow, and keeps
  the fork a look-port that is easy to rebase. A VS Code player for Python can
  reuse the plan format later, using `WorkspaceEdit` on the cell document.

## 5. Rejected alternatives

- OS-level keystroke automation (the clx attempt): brittle for the reasons
  in §1.
- Timed auto-typing without presenter control: it swaps the typing burden
  for keeping up with a timer. Presenter pacing (step or hacker) replaces it.
- Diffing at playback time without verification: replaced by a plan that is
  checked by replay and has a guaranteed fallback.

## 6. Roadmap

1. clm: the opt-in `recording-code-along` output kind carrying
   `metadata.clm.typing` (#1023, decisions settled).
2. Package the extension for pip, install it in the Docker image, and dogfood
   on a real C++ deck with a live kernel. Compare the two modes in real
   recordings.
3. The sidecar overrides from §4 (edit order) with `clm validate` stale
   detection.
4. The voiceover "trigger the next step" marker, then a VS Code player.
