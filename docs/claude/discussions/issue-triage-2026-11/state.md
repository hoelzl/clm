---
status: active
owner: maintainers
updated: 2026-11-16
review-by: 2027-05-16
---

# State: open-issue triage and fix batch, November 2026

S25. The owner asked for a triage of the open clm issues: which can be
resolved now, which need design, which bring the most value. The agent
then fixed them in parallel with subagents, and settled the remaining
decisions with the owner. Task state is in the PRs and issues below.
There is no handover.

## Settled

- **Order of work.** The agent proposed three groups of sync issues:
  answer handling, regression tests on the sequence core, and fixes inside
  the positional engine. The owner asked whether waiting for phase D (the
  switch to the sequence core) would put the highest-priority fixes on hold
  for two weeks. Result: groups 1 and 2 were done now, and group 3 waits
  until after phase D. Positional-only fixes would be thrown away in
  phase E.
- **Agent correction, #1228.** The agent's triage listed #1228 (C++23) as
  open work. The owner remembered it as largely done, and the code
  confirmed this. The agent updated #1228 and #1163 with the owner's
  permission.
- **Agent correction, #1193.** The agent's triage called it trivial. Its
  subagent found that `uv.lock` is shipped to students on purpose. The
  owner closed it as intended.
- **#1245** is covered by #1251 (S24, `drawio-render-integrity`).
- **Decisions, all as the agent recommended.**
  - #1216: the sequence path must lift the load-time `idless_anchor`
    refusal. This is phase D work.
  - #1195: keep the notebook-output epoch bump; the owner rebuilds.
  - #1090: closed, with follow-up #1270.
- **Whisper default (#1105).** The owner had set
  `CLM_WHISPER_CONDITION_ON_PREVIOUS_TEXT` globally. A side agent warned
  that a global non-default key re-transcribes every video, so the owner
  deleted the variable and asked for a measured comparison instead. The
  agent ran an A/B test on two German and two English lecture videos
  (about 69 minutes). Results are in the comment on #1105.
  - The faster-whisper default (`True`) looped (14 loop segments in one
    English video), inserted clauses that were never spoken, and produced
    the hallucination "ARD Text im Auftrag von Funk".
  - `False` had no loops, and its word error rate against Auphonic fell
    from 3.9% to 2.2%.
  - The costs of `False` are longer German segments and some unpunctuated
    stretches.
  - The owner chose **option A**: `False` becomes the default, and the
    `{}` cache key is kept. Old transcripts (decoded with `True`) are still
    served; a video is redone with `--refresh-cache`. Option B, which
    re-keys and re-transcribes everything, was rejected because it repeats
    the mass re-transcription. Shipped in #1279.
- **#1162 follow-up: show the model bullet structure.** The owner had no
  strong intuition and asked for the agent's view. The agent recommended
  logical bullets: nesting kept, continuation lines unwrapped, and the
  writers re-wrap afterwards. The owner agreed. The agent found that inline
  narration today reaches the model as a single space-joined line
  (`_strip_code_comments`). Filed as #1280.

## Shipped

- **Agent-run PRs:** #1253–#1265, #1267–#1269, #1271, #1279, #1281.
- **#1281** fixed master. #1241 (the static command index) and #1251
  (`cache forget`) were merged without seeing each other, which is a
  semantic merge conflict. The cause is commented on tracker issue #1272,
  which the owner had closed once master CI was green again.
- **Worktrees:** all of this session's worktrees and the four empty
  leftover directories were removed at the owner's request.
- **The save:** S25 was saved in #1282 and extended once, after #1272
  was closed.

## Open

- Phase D carries #1216 and the sync group 3 fixes (positional-engine
  defects).
- #1280: narration given to the model as logical bullets.
- #1270: the build-abort follow-ups to #1090.
- From the triage, the issues that need design are unchanged. They are
  listed in the transcript under "Needs design first".

## Next boundary

Phase D (the sequence-core default), after the dogfood window (2 weeks or
50 passes, O4 in `sync-structural-core`).
