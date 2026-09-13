---
status: active
owner: maintainers
updated: 2026-09-13
review-by: 2027-03-13
---

# Discussions

Continuity for *conversations*, as distinct from continuity for *tasks*.

A handover in `docs/claude/handovers/` carries task state: phases, decisions
already made, what is done, what is next. A lot of real work in this repo is
a conversation that occasionally spawns tasks — a design debate about the C++
export, a review of what a sync outcome should have been, an owner correcting
an agent's priors — and forcing that shape into a handover is what makes
handovers awkward to enter and leave. The argument for a decision, in the
owner's own words, lands nowhere else.

## Four layers

Each layer is defined by its **read trigger** and its **mutability**, not by
its content. Those two rules keep the layers from collapsing into each other.

| Layer | Read trigger | Mutability | Size |
| --- | --- | --- | --- |
| `<thread>/state.md` | Any contact with the topic | Rewritten every time | ~1k words |
| `register.md` (single, at this root) | Deciding which transcript to load | Appended; never rewritten | ~1k words |
| `<thread>/transcripts/*.md` | Resuming the *discussion* rather than acting on its outcomes | Snapshot; re-run to extend | see register |
| Raw session source (Claude `.jsonl` / Hermes `state.db`) | Archaeology; the cleaned transcript proved insufficient | Immutable, outside the repo | harness-dependent |

Subdirectories are individual **threads**, named by subject rather than date
(`cpp-ide-export`, `sync-identity-model`), each carrying `state.md` plus
`transcripts/`. The **register is one file for the whole tree** —
`docs/claude/discussions/register.md` — because the session numbering is a
single sequence across all threads: one index is where a gap in that sequence
is visible, and a per-thread register would scatter it.

## Doc-currency headers

Every file in this tree carries a status header, checked by
`python scripts/check_doc_currency.py` (the rest of `docs/` in this repository
does not use the convention; it applies here because it is what tells a
rewritten `state.md` from a frozen transcript):

```
---
status: active | superseded | archived
updated: YYYY-MM-DD
review-by: YYYY-MM-DD      # required while status == active
---
```

- `state.md` and `register.md` are `status: active` with a `review-by`; the
  save procedure bumps both dates on every save. A dormant thread going past
  its `review-by` is a soft warning, which is the intended nudge to re-verify
  it against live state before citing it.
- Transcripts are `status: archived` — a transcript is a frozen record of what
  was said, never a current-truth document. `scripts/clean_transcript.py`
  emits that frontmatter itself; never hand-edit a transcript.

## Why there is a transcript layer at all

The register was originally meant to carry the discussion forward in condensed
form. It cannot, and writing a better one does not fix it.

The test that settles it: would a session that read only the index produce the
same quality of contribution as one that had been present? No — the useful
remarks in a conversation come from holding two *incidental* things together,
and neither is a conclusion. Summarising preserves conclusions and discards
exactly the texture cross-connections are made from. **You cannot summarise
your way to generativity.**

The objection that transcripts are enormous is false. A session file is ~95%
machinery: tool calls, tool results, harness metadata. `scripts/clean_transcript.py`
keeps the dialogue verbatim, reduces tool calls to one-line stubs so references
in the dialogue stay coherent, and drops the rest (10:1 to 100:1 on real
sessions). That is cheap enough to simply load. Exact sizes live in the
register's session table and nowhere else.

## Raw versus cleaned

**Raw** transcripts are never committed. They are large, low-density, and
carry incidental personal context, absolute profile paths, and possibly
secrets.

A **cleaned** transcript is a different artifact: small, redacted (home
directories → `~`, Hermes profile paths → a placeholder, e-mail addresses →
`<email>`), and curated to the conversation. Those are committed here, and the
cleaning step carries the redaction duty that the raw rule was protecting.

The raw session sources stay outside, referenced by Claude `.jsonl` name and
project slug, or by Hermes session ID and profile, from the register. They
remain the evidence base — the thing that lets the upper layers be checked
rather than trusted.

## What goes in the register

One row per thread: what it was, its disposition, where it landed (a PR, a
handover, a design doc, an issue), which sessions. Nothing that the transcript
already carries better.

Two things earn more than a row, because both are cheap to state and expensive
to rediscover:

- **Dropped threads.** A tangent that went nowhere has no state, so it is
  invisible in `state.md` — but "we discussed this and set it aside" is real
  information, and without it the thread gets re-opened from scratch.
- **Where the agent was wrong.** A fresh session arrives holding exactly the
  priors that were already corrected, and each was a natural thing to think.
  One line per correction, pointing at the transcript for the reasoning.

Everything else belongs to the transcript. If a section of the register could
be replaced by "load the transcript" without loss, delete it.

## The failure mode to watch

**A session's conversation layer exists only if somebody runs the save before
that session ends.** The outcomes land in commits, PRs and handovers; the
argument for them exists nowhere else.

The guard is not "remember at the end", which is exactly what fails. The save
is **cheap and re-runnable — re-running overwrites with a longer transcript** —
so the guard is to run it at the first natural boundary and again at the last.
A session saved twice costs one command; a session saved never costs the
conversation.

**Numbering sessions is load-bearing**, because a hole in the sequence is the
only signal that a conversation was lost. A register that recorded sessions by
date alone would show nothing missing — there is no date at which a session is
*expected*. If a save is skipped, record it as deliberately skipped so the gap
is explained.

### Two mechanisms

They split the problem along the line that matters: **cleaning needs no
judgement, curation does.**

**`python scripts/audit_transcripts.py` answers "which sessions were never
saved?"** It lists the Claude sessions of this repository — the main
checkout's project slug *and* every `.claude/worktrees/<name>` slug, since
most sessions here run from a worktree — and, when `HERMES_HOME` is set, the
Hermes sessions recorded under the checkout, that no register mentions, with
each one's date, owner-turn count and opening line. The owner-turn count is
the triage signal: a two-turn build session has little conversation to resume,
while a sixteen-turn one is where forks were argued. It prints and exits 0 —
an unsaved session is a judgement call, not a broken tree, so it is
deliberately not a CI gate.

**A staging hook can snapshot every Hermes session automatically.** Wired per
Hermes profile (`hooks.on_session_end` in the profile `config.yaml`),
`scripts/stage_hermes_session.py` stages every session turn-end into
`~/.clm-transcripts/`, including archived pre-compaction rows that no CLI
export path returns. Nothing is committed and nothing is placed — deciding
*which thread* a conversation belongs to, and what `state.md` should now say,
stays with the save-discussion skill. Claude Code needs no hook: its session
files under `~/.claude/projects/<slug>/` are the staged copy.

## What the transcript structurally cannot cover

A transcript records a conversation. It cannot record what an agent did
*alone* after the last exchange — and in this repo that is often where the
work landed (the PR, the corpus run, the merge). So `state.md` carries the
obligation: when saving, it must reflect work done *after* the last exchange,
not merely what was said. That is the only layer positioned to do it.

## Finding this at all

Canonical discussion skills live under `.agents/skills/` (`save-discussion`,
`save-knowledge`, `resume-discussion`). Claude Code discovers them through
thin `.claude/skills/` adapters; other harnesses read the neutral root. The
harness-neutral route is the pointer to this directory in `AGENTS.md`, which
every harness loads.

## Goal

Not transcript fidelity. Someone resuming should arrive as a participant with
imperfect memory rather than as a stranger who read the minutes.
