---
status: active
owner: maintainers
updated: 2026-09-25
review-by: 2027-03-25
---

# State: decoupling the schedule from the section-as-week assumption (#916)

Decided 2026-09-25 (S8 — transcript under
`recordings-rerecord-backlog/transcripts/2026-09-25-s8.md`, one session
covering three threads). No design note: the decision is written where the
issue asked for it — a "Sections, weeks and teaching days" section in
`clm info calendar` and a "Sections are weeks by convention, not by
structure" note under `<subsection>` in `clm info spec-files` (the docs PR
closes #916). One follow-up: #1009 (`clm release section` alias).

## Settled (S8)

- **Sections are weeks by convention.** `export schedule` renders a
  section as a week because the AZAV certification listing *is* a
  week/weekday grid; nothing in the build, the validator or the calendar
  requires five teaching days.
- **The decoupling already exists.** The cohort calendar consumes
  subsections as a flat bucket sequence (one bucket per subsection,
  `span` = weekday-token count, 1 for a thematic group) and places them on
  consecutive teaching dates; section boundaries are invisible to
  placement (design §4, `build_buckets`). A non-week section is written as
  name-only subsections, one per teaching day, no `weekday=` — the
  schedule shows the name as the day label, `duplicate_weekday` /
  `weekday_out_of_order` / `--check-workdays` do not apply, and the
  calendar needs an explicit `pattern` only when no subsection in the
  course carries a weekday (`no-teaching-weekdays`).
- **Of the four proposed shapes**: teaching-day subsections — already the
  model; date-anchored `export schedule --calendar` — rejected as a
  duplicate of `calendar generate -f md|csv` (the CSV carries `date` and
  the real `weekday`); per-section workday checks — not needed (the check
  is opt-in and exempts name-only subsections); "week"→"section" aliasing
  — accepted, naming only (#1009).
- **Rejected**: migrating `weekday=` away (it labels the grid *and*
  derives the calendar pattern); renaming the schedule's week grid.

## Corrections of the agent's priors

- The issue's item 2 ("a sixth teaching day has nowhere to go") assumed
  `weekday=` is required; it is optional, and a name-only subsection is a
  teaching day with a label. The issue's item 1 (section-relative labels
  are fiction for a mid-week start) is true and by design — the calendar
  is the dated view.
- `clm info calendar` already said "Buckets are the atomic content units …
  their order is fixed by the spec" but never said section length does not
  matter; the gap was documentation, not mechanism.

## Open (owner)

- None.

## Deferred / revisit conditions

- A per-section "this really is a full week" assertion — only if a course
  wants `--check-workdays` on some sections and not others.
- The schedule CSV's `weekday` cell is empty for name-only subsections;
  whether a certification authority accepts a named day instead of a
  weekday has not been tested against a real submission.

## Next conversational boundary

None expected. #1009 is a naming-only implementation issue.
