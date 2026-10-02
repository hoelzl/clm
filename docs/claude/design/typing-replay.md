# Typing replay for recording notebooks

**Status**: STEPS 1–2 SHIPPED 2026-10-02. clm emits the opt-in
`recording-code-along` kind (#1023, PR #1027). The extension is packaged and
installed in the cam-notebook images (0.5.4), and it was verified against a live
xcpp20 kernel. Not yet done: real recordings, then steps 3–4 (§6).
| **Created**: 2026-10-02
**Extension**: GitHub hoelzl/jupyterlab-clm-typing (public, MIT), checked out
at `~/Programming/Python/Projects/jupyterlab-clm-typing`. Install with
`pip install git+https://github.com/hoelzl/jupyterlab-clm-typing` (the prebuilt
labextension is committed, so no Node is needed). Its README has the dev
workflow and the verification log.
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
    can't leak characters into it. Every run-cell chord (Shift, Ctrl, Alt or
    Cmd + Enter) disarms it and still runs the cell; this needs 0.1.1 or later
    (0.1.0 only knew Shift+Enter; the owner asked for the rest). Esc or the
    hotkey disarms it without running. Arrow keys and Space are swallowed too,
    so press Esc before navigating slides. The spike originally disarmed itself
    at the end, and the over-typed characters landed in the cell.
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

**Live kernel (2026-10-02, `cam-notebook:0.5.2-cpp`, Notebook 7.6 /
JupyterLab 4.6.1, xcpp20).** The wheel was pip-installed into the running
container, and the extension loaded on page reload without a server restart.
Two CppCourses decks ("Member Functions", "Structs und Klassen") were tested,
first through the extension repo's stand-in `scripts/merge_typing.py`, then
as clm-built `recording-code-along` notebooks:

- Every cell was typed and then run in order with no compile errors, after a
  fresh kernel restart. That covered the struct→class rewrite of `MyComplex`
  in 22 step-mode steps and the `c.re = 3` → `c.set_re(3)` in-place edits.
- Hacker mode took about 100 real key presses plus a real Shift+Enter.
- A deck's *first* cell can be a start/completed pair (`struct Point` gains
  `distance`). Running it without typing it makes later cells fail to
  compile, so a recording must type it.

**Still unverified:** real recording ergonomics (which mode reads better on
video), and slide navigation with an armed cell.

## 3. Data contract (implemented, #1023)

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
carry the key. A `start` cell and its `completed` cell merge into one cell
(start source, completed target). The player computes the plan at load time,
so clm doesn't emit plans in V1.

**Implementation** (`RecordingCodeAlongOutput` in
`src/clm/workers/notebook/output_spec.py`; tests in
`tests/workers/notebook/test_recording_code_along.py` and
`tests/core/test_recording_code_along_kind.py`):

- The metadata is attached in `annotate_cells`, on the full source cell list
  before filtering.
- Pairing mirrors the validator: the next same-language `completed` cell,
  whatever sits in between (kept, blanked or `del` cells), unless another
  `start` comes first.
- The kind is opt-in (excluded from `ALL_KINDS` and the default targets) and
  notebook-only (`kind_supports_format`, enforced in `output_specs` and
  `OutputTarget.should_generate`). It is never bundled into JupyterLite, and a
  target that lists it without `notebook` fails validation.
- The private routing sets now all read `PRIVATE_KINDS`. Without that, a target
  listing only this kind got no private image copies.

**Landmines found while implementing:**

- clm output cell ids are **positional** (`CellIdGenerator.set_cell_id(cell,
  index)`). Pairing code-along and completed *outputs* by id only works by
  coincidence; pair by source tags.
- Every real target is **explicit** (`OutputTarget.from_spec` → `skip_toplevel`),
  so `PRIVATE_KINDS` routing to a `speaker/` toplevel never applies. Privacy
  depends only on which target lists the kind: list it on a private target
  such as `speaker`. The info topic `clm info spec-files` says so.

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

1. **Done:** clm emits the opt-in `recording-code-along` output kind carrying
   `metadata.clm.typing` (#1023, PR #1027).
2. **Done except recordings:** the extension is pip-installable and pinned by
   commit in PythonCourses `docker/cam-notebooks`, next to the RISE pin
   (`TYPING_REF` in `Dockerfile.split`, a literal SHA in `Dockerfile`;
   PythonCourses `4e09d7d`). The 0.5.4 images, with extension 0.1.0, are on
   Docker Hub. Extension **0.1.1** (`3cac2b0`) goes out with the 0.5.6 images,
   which another agent owns. Verified with a live kernel (§2).
   **Still to do:** compare the two modes in real recordings.

   **Image-layer cost of an extension bump.** A pin change in the `base` stage
   invalidates every layer built on it: the C++ toolchain, .NET and the ML stack.
   Rebuilt layers are not byte-identical, so the push re-uploads gigabytes. The
   cheap pattern is to install the update as the **last layer** of each target.
   That was measured for 0.5.5-cpp: 20 of 21 layers kept their digests, and the
   new data was a 156 kB extension layer plus a 4 kB `WORKDIR` layer. It is
   drafted as `TYPING_UPDATE_REF` (uncommitted PythonCourses edit, handed to
   the 0.5.6 agent). When a release rebuilds the lower layers anyway, fold the
   update into `TYPING_REF`.
   **On-screen cheat sheet** for recording (all keys, including the RISE fork's):
   private artifact https://claude.ai/artifact/9gUrMUj3EMgj3uPVqwFHUG.
3. The sidecar overrides from §4 (edit order) with `clm validate` stale
   detection.
4. The voiceover "trigger the next step" marker.
5. **VS Code player — spike done 2026-10-02, ahead of order.** It lives in the
   same repo as `vscode/` (the owner chose this over adding it to
   jupyter-slide-nav, which stays generic) and imports the shared
   `src/planner.ts`/`player.ts`, so both players run identical plans. Edits
   go through `TextEditor.edit()`, which bypasses auto-close and on-Enter
   indent. Hacker mode registers VS Code's `type` command only while armed.
   An integration suite (`@vscode/test-electron`) covers both modes. The owner
   tested it on real notebooks. LANDMINE: in notebook command mode,
   `activeTextEditor` still points at the cell's editor, and
   `notebook.cell.edit` toggles out of edit mode when the cell is already
   editing. To enter edit mode, use `showTextDocument(cell.document)`.
   PR hoelzl/jupyterlab-clm-typing#1.
