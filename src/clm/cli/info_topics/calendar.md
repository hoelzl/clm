# CLM {version} — Cohort Calendar Reference

A cohort calendar projects a course's content schedule onto the real teaching
dates of a specific cohort. Each cohort has its own `.calendar.toml` file that
records the start date, teaching-day pattern, holidays, and any adjustments.

## Commands

```
clm calendar generate <spec>   # Render the cohort schedule (md/csv/ics)
clm calendar check  <spec>   # Validate the calendar file; non-zero on errors
clm calendar status <spec>   # Show today's position vs plan
clm calendar push   <spec>   # Mirror the calendar into a Google calendar
```

### Common flags (all four commands)

| Flag | Description |
|---|---|
| `--channel NAME` | Cohort channel name; resolves `release/<NAME>.calendar.toml` beside the channel ledger |
| `--calendar PATH` | Explicit path to the `.calendar.toml` file (overrides `--channel`) |
| `--data-dir PATH` | Course data directory containing `slides/` (default: auto-detected) |

Exactly one of `--channel` or `--calendar` is required. `check`, `status`
and `push` also take `--json` (CLM {version}+, issue #966): the result is one
JSON document on stdout, diagnostics stay on stderr, exit codes are
unchanged — see [JSON output](#json-output-for-agents) below.

### `clm calendar generate` flags

| Flag | Description |
|---|---|
| `-f md\|csv\|ics` | Output format: Markdown table (default), CSV, or RFC 5545 iCalendar |
| `-L de\|en` | Language for titles (default: `de`) |
| `-o FILE` | Write output to a file |
| `-d DIR` | Write output to a directory |

### `clm calendar status` flags

| Flag | Description |
|---|---|
| `-L de\|en` | Language for titles (default: `de`) |
| `--as-of DATE` | Reference date in YYYY-MM-DD (default: today) |
| `--json` | Emit the status as a JSON document (see below) |

### `clm calendar push` flags

*New in {version}.* Requires the `[gcal]` extra
(`pip install "coding-academy-lecture-manager[gcal]"`).

| Flag | Description |
|---|---|
| `--calendar-id ID` | Target Google calendar id (default: `calendar_id` from the TOML's `[google]` table) |
| `--credentials PATH` | Google credentials JSON (env: `CLM_GOOGLE_CREDENTIALS`): an OAuth "Desktop app" client (one-time browser consent, cached token) or a service-account key the calendar is shared with |
| `-L de\|en` | Language for event titles (default: `de`) |
| `--dry-run` | Print the insert/update/delete plan; change nothing |
| `--json` | Emit the plan as a JSON document — with `--dry-run` before anything touches Google, otherwise the plan that was applied |

The push only touches **CLM-managed events**: each event is tagged (private
extended properties) with the cohort namespace and the same stable
per-assignment UID the ICS output uses, so re-pushing updates events in place,
deletes events whose assignment disappeared, and never disturbs other events
in the same calendar. Events are all-day and marked free (transparent).
Each event uses the same title/body format as the `.ics` feed (see
[Event format](#event-format) below). Projection errors (see
`clm calendar check`) block the push.

## JSON output for agents

The calendar TOML is hand-edited and clm never writes it, so when an agent
maintains a cohort calendar it *is* the editor. `--json` gives it structured
findings instead of prose (CLM {version}+, issue #966). In every case stdout
carries exactly one JSON document, diagnostics and errors go to stderr, and
the exit code is unchanged.

`clm calendar check --json`:

```json
{
  "ok": false, "errors": 1, "warnings": 1,
  "findings": [
    {"severity": "error", "rule": "end-overflow", "anchor": "end",
     "dates": ["2026-03-02", "2026-03-20"],
     "message": "content does not fit before end 2026-03-20: needs 12 teaching dates, 10 available — merge ≥ 2 bucket(s)."},
    {"severity": "warning", "rule": "insert-not-teaching-date",
     "anchor": "adjustments[insert 2026-03-07]", "dates": ["2026-03-07"],
     "message": "insert date 2026-03-07 is not a teaching date (wrong weekday or a holiday); it will not appear."}
  ]
}
```

`rule` is stable; `anchor` names where in the TOML the finding attaches — a
top-level key (`pattern`, `end`), an adjustment (`adjustments[pin REF]`,
`adjustments[split REF]`, `adjustments[insert DATE]`, `adjustments[pin A, pin B]`
for a pair), or a projected `segment START..END` between pins; `dates` are the
projected ISO dates involved.

| Rule | Severity | Anchor |
|---|---|---|
| `no-teaching-weekdays` | error | `pattern` |
| `unknown-ref` / `ambiguous-ref` | error | `adjustments[pin REF]` / `adjustments[split REF]` |
| `duplicate-pin` / `pins-out-of-order` | error | `adjustments[pin A, pin B]` |
| `segment-overfull` | error | `segment START..END` |
| `end-overflow` | error | `end` |
| `segment-free-dates` | warning | `segment START..END` |
| `insert-not-teaching-date` | warning | `adjustments[insert DATE]` |

`clm calendar status --json` (with `--as-of` for a deterministic answer):

```json
{
  "as_of": "2026-03-02", "language": "en",
  "state": "class-today",
  "current": {"start_date": "2026-03-02", "end_date": "2026-03-02", "kind": "video",
              "label": null, "plan_label": "W1 Monday", "section_title": "Week 1",
              "date_label": "Monday 2026-03-02", "content": "Intro; Law",
              "summary": "Intro (+1 more)",
              "decks": [{"title": "Intro", "module": "module_100", "topic_id": "intro",
                         "deck_file": "slides_010_intro", "number_in_section": 1}],
              "bucket_refs": ["module_100/intro/slides_010_intro"], "activity_labels": []},
  "reference": {"…": "the assignment drift was measured against (current, else next)"},
  "upcoming": [{"…": "the next assignments, same shape"}],
  "drift_days": 0,
  "has_errors": false,
  "plan": [{"…": "every projected assignment, same shape — the whole calendar"}]
}
```

`state` is one of `class-today`, `no-class-today`, `not-started`, `finished`,
`empty`. `drift_days` is `+behind` / `-ahead` / `0` on plan, or `null` when
nothing to measure. `plan` is the full projection (the same rows
`generate` renders), so an agent can reason past the five-row `upcoming`
lookahead.

`clm calendar push --dry-run --json` (the plan; identical shape without
`--dry-run`, then with `"dry_run": false, "applied": true`):

```json
{
  "dry_run": true, "applied": false,
  "calendar_id": "abc123…@group.calendar.google.com", "namespace": "jan",
  "inserts": [{"uid": "…", "start_date": "2026-03-02", "end_date_exclusive": "2026-03-03",
               "summary": "Intro (+1 more)", "description": "Week 1\n\n01  Intro\n02  Law"}],
  "updates": [{"event_id": "…", "uid": "…", "start_date": "…", "end_date_exclusive": "…",
               "summary": "…", "description": "…"}],
  "deletes": [{"event_id": "…", "label": "2026-03-09  Old title"}],
  "unchanged": 4,
  "totals": {"inserts": 1, "updates": 1, "deletes": 1, "unchanged": 4}
}
```

## Calendar file format

Calendar files live at `release/<channel>.calendar.toml` alongside the channel
ledger. They are hand-edited TOML; CLM never writes them.

```toml
# Required: first teaching date
start = 2026-03-02

# Optional: last allowable teaching date (enforced by check)
end = 2026-06-30

# Optional: teaching weekdays. Omit to derive from the spec's <subsection weekday> values.
# Canonicalized to Mon-Sun order; duplicates removed.
pattern = ["mon", "wed", "fri"]

# Optional: dates excluded from the teaching sequence
holidays = [
    2026-04-06,                                          # single date
    {from = 2026-07-20, to = 2026-08-02, label = "Summer break"},  # inclusive range
]

# Optional: adjustments (array of tables, applied in file order)
[[adjustments]]
merge = 2026-03-18   # collapse the next `count` buckets onto one date (catch-up)
count = 2

[[adjustments]]
split = "variables_intro"   # spread one bucket across multiple dates (slow down)
dates = [2026-03-25, 2026-03-26]

[[adjustments]]
insert = 2026-03-30  # teaching date with no new video (review, exam, guest)
label = "Review & Q&A"

[[adjustments]]
pin = "control_flow"  # anchor: this bucket lands on exactly this date
date  = 2026-04-09    # also segments the timeline — miscounts can't cascade past a pin

# Optional: default target for `clm calendar push`
[google]
calendar_id = "abc123...@group.calendar.google.com"
```

### Weekday tokens

`mon`, `tue`, `wed`, `thu`, `fri`, `sat`, `sun` (lowercase, three-letter).

### Bucket references (for `pin` and `split`)

A bucket reference is a **topic ID** or **deck-file stem** (e.g.
`slides_010_introduction_ml`). These are stable identifiers that do not change
when the spec is reordered; use them instead of week/weekday coordinates.
Unknown or ambiguous references are reported as errors by `clm calendar check`.

## Sections, weeks and teaching days

CLM has two views of "when is this taught", and they deliberately use
different time models (issue #916, decided 2026-09-25):

- **`clm export schedule` is course-relative and renders one `<section>` as
  one week.** That is a *presentation convention* for the certification
  listing (AZAV wants a week/weekday grid), not a structural rule: a
  section is a thematic block, and the week is what the schedule makes of
  it. The day labels come from the declared `weekday=` tokens (or the
  subsection `<name>`), never from real dates — a section that a cohort
  starts on a Wednesday still lists "Montag" first, by design. For real
  dates use the calendar.
- **The cohort calendar is date-anchored and ignores section length.** It
  consumes the schedule's subsections as a **flat sequence of buckets**
  (one bucket per `<subsection>`, multi-weekday subsections span several
  dates) and lays them on consecutive teaching dates. A section with three
  or six subsections simply consumes three or six teaching dates; nothing
  aligns sections to calendar weeks. The `plan_label` (`W4 Tuesday`) and
  `section_title` in `calendar status --json` are the section-relative
  coordinates carried along for drift reporting, not placement inputs.

**A section that is not a Mon–Fri week** is expressed with name-only
subsections, one per teaching day, and no `weekday=`:

```xml
<section>
    <name><de>Block 7: Agenten</de><en>Block 7: Agents</en></name>
    <topics>
        <subsection><name><de>Tag 1</de><en>Day 1</en></name>
            <topic>langgraph_intro</topic>
        </subsection>
        <subsection><name><de>Tag 2</de><en>Day 2</en></name>
            <topic>langgraph_branching</topic>
        </subsection>
        <!-- … six days if the block needs six -->
    </topics>
</section>
```

`export schedule` shows the `<name>` as the day label; the calendar places
each subsection on the next teaching date; `duplicate_weekday`,
`weekday_out_of_order` and `--check-workdays` do not apply to name-only
subsections. Two consequences: a course whose subsections carry **no**
`weekday=` at all must set `pattern` explicitly in the calendar TOML (there
is nothing to derive it from — `check` reports `no-teaching-weekdays`), and
`weekday=` remains the right choice for courses whose sections really are
Mon–Fri weeks, because it both labels the listing and derives the pattern.

**Vocabulary**: `clm release week` selects a *section* (its docs say so);
the name is historical — a section is a thematic block, a week is what the
schedule or the calendar makes of it.

## Projection rules

1. **Teaching dates** are generated from `start` forward on the days in
   `pattern`, with `holidays` removed.
2. **Buckets** are the atomic content units from `clm export schedule`; their
   order is fixed by the spec.
3. **Pins segment the timeline.** Each pin creates an independent fitting
   segment; content before the pin fits independently of content after it.
4. **Adjustments** are applied in file order: inserts add empty dates, merges
   collapse buckets, splits spread one bucket across multiple dates.
5. **Over-full segments** (more buckets than teaching dates) are **errors**;
   `check` exits non-zero with the exact deficit.
6. **Under-full segments** (free teaching dates) are **warnings**; `check`
   exits 0.

## Validation (`clm calendar check`)

Errors cause a non-zero exit:
- Unknown or ambiguous `pin`/`split` bucket reference
- More buckets than teaching dates in a segment (reports exact deficit)
- Content extends past `end`
- Pin date is not a teaching date (wrong weekday or falls in a holiday)

Warnings (exit 0):
- Holiday falls on a non-teaching weekday (no-op)
- Segment has more teaching dates than buckets (free dates)
- Insert date is not a teaching date

## ICS output

Each assignment becomes an all-day VEVENT. UIDs are derived from stable
bucket-ref seeds so re-exporting the same calendar produces the same UIDs.
Multi-day assignments (e.g. a bucket spanning Mon–Tue) are emitted as a single
multi-day event. The `DTSTAMP` is fixed to the start date for determinism.

## Event format

Both the `.ics` feed and the Google Calendar push (`clm calendar push`) format
each day's entry the same way, so a day with several decks stays readable:

- **Title** (`SUMMARY`): the day's first deck title, plus a `(+N more)` /
  `(+N weitere)` count when the day carries more than one deck. A single-deck
  day is just that deck's title; an `insert` is its label.
- **Body** (`DESCRIPTION`): the section name (e.g. `Woche 03: LLM-APIs in der
  Praxis`), then one line per slide. Each slide is prefixed with its **section
  number** — the same `01`, `02`, … that appears in the built output filenames
  — so students can find the slide quickly in a large course. Inserts have no
  body.

The wording (`more` vs `weitere`, etc.) follows `-L`. Because the body changed,
the first `clm calendar push` after upgrading updates every managed event once;
subsequent pushes are no-ops until the schedule changes.

## Examples

```bash
# German Markdown to stdout
clm calendar generate course.xml --channel jan

# English iCalendar file for student subscriptions
clm calendar generate course.xml --channel jan -f ics -L en -o jan.ics

# CSV for spreadsheet import
clm calendar generate course.xml --calendar release/jan.calendar.toml -L en -f csv

# Validate before pushing
clm calendar check course.xml --channel jan

# Show where the cohort is today
clm calendar status course.xml --channel jan -L en

# Check status as of a specific date
clm calendar status course.xml --channel jan --as-of 2026-04-15

# Preview, then push the calendar to Google Calendar
clm calendar push course.xml --channel jan --dry-run
clm calendar push course.xml --channel jan --credentials oauth-client.json
```
