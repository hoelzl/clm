# Cohort Viewing Calendar

**Status**: Design proposal
**Date**: 2026-06-09
**Author**: design session (trainer + AI)
**Tracking issue**: TBD

## 1. Problem

`clm export schedule` produces the *ideal* plan of a course in **course-relative
time** — "Week 3, Tuesday: watch these decks". That format is right for planning
the course but wrong for two things students actually need:

1. **Real calendar dates.** Students want "Tuesday, 17 March: watch these two
   videos", not "Tuesday of Week 3".
2. **Reality, not the plan.** Real cohorts deviate from the ideal: public
   holidays, a delayed start, a two-week break, days where many questions ate
   the time, days where we deliberately double up to catch back up. After a
   couple of holidays the plan's "Week N" no longer lines up with the real
   calendar week, and that drift compounds.

We need a per-cohort, student-facing artifact that maps the course's ordered
content onto **real dates**, and a mechanism for the trainer to keep it
matching reality **without re-dating the whole course by hand every time
something slips**.

There can be multiple cohorts of one course, and cohorts of different courses,
running simultaneously — each with its own dates. The mechanism must support
that with no shared mutable state between cohorts.

### Non-goals

- **Not** solution-release timing. This artifact answers *"watch what, by
  when"* only. When students receive solutions stays the separate, manual
  `clm release week` / `clm release sync` decision (see §9). The two layers may
  share date math in a future iteration, but this design keeps them
  independent.
- **Not** a re-authoring of the course plan. The course-relative `schedule`
  (weeks, weekday subsections) remains the single source of *content order*,
  shared by every cohort.

## 2. Terminology

| Term | Meaning | Time model | Scope |
|---|---|---|---|
| **schedule** | The course's ordered plan (existing `clm export schedule`): weeks → weekday subsections → decks. | Course-relative (Week N, Montag) | Per course (shared by all cohorts) |
| **calendar** | The per-cohort projection of that plan onto real dates. *The new artifact.* | Real dates | Per cohort |
| **assignment** | One row of a calendar: a date (or date span) and the decks/videos due by it. | Real date | — |
| **bucket** | One unit of the content sequence: the decks of one teaching day, derived from the schedule. | — | — |
| **channel** | An existing cohort definition (`<release-channels>` / `<channel>`). The calendar attaches to a channel. | — | Per cohort |

We keep `schedule` for the plan and introduce **calendar** for the dated,
student-facing artifact. Each line of a calendar is an **assignment**.

## 3. Core idea: project, then patch

The naïve implementation — a hand-maintained table of `date → videos` — is
unmaintainable: one inserted holiday forces re-dating every row below it, so the
trainer stops updating it. Instead we model the calendar as a **zip of two
independent sequences**, and the trainer only ever edits the small deltas from
the ideal:

1. **Content sequence** — the ordered list of *buckets*, derived from
   `export schedule`. Shared by all cohorts of the course. Deck-granular.
2. **Date sequence** — the real teaching dates, *generated* per cohort from four
   small inputs: a start date, a weekly teaching pattern, a holidays list, and
   an ordered list of adjustments.

```
calendar = zip(content_sequence, date_sequence)     # default 1 bucket : 1 date
           then apply adjustments in order
```

The trainer never writes down the date of a video. Dates are computed. The
trainer maintains only `holidays` and `adjustments` — typically a handful of
lines for an entire course.

### Why this absorbs deviation cheaply

- **Holiday / break** → a date (or interval) is removed from the date sequence,
  so every bucket after it slides one teaching-date later, **automatically, with
  zero downstream edits**. "Two holidays → two three-day weeks" is two
  `holidays` entries. A two-week break is one interval entry. The plan's "Week 4"
  simply lands in real-calendar week 5 or 6 — fine, because week numbers are
  plan labels, never dates.
- **Catch-up** → one `merge` adjustment puts two buckets on one date; everything
  after pulls one teaching-date earlier. One line.
- **Slow down** → one `split` adjustment spreads one bucket across two dates.
- **Anchor** → a `pin` nails a specific bucket to a specific date regardless of
  drift, and *segments* the timeline so an early miscount can't cascade past it
  (see §6.3).

## 4. The content sequence (from `schedule`)

The content sequence is the existing schedule's day-buckets, in order:

- Walk weeks (sections) in declared order.
- Within a week, walk subsections in document order.
- Each subsection yields **one bucket**, carrying that subsection's decks
  (deck-granular: title + topic id + deck file, exactly what `export schedule`
  already computes).

### 4.1 Multi-weekday subsections occupy multiple teaching dates

A `<subsection weekday="mon,tue">` **represents two teaching days** and therefore
**consumes two teaching dates**. We model this with a `span`:

```
bucket.span = max(1, number of weekday tokens on the subsection)
```

- `weekday="mon"` → span 1
- `weekday="mon,tue"` → span 2
- subsection with no `weekday` (thematic group) → span 1

A bucket with span *N* occupies *N* consecutive teaching dates. The spec does
**not** record which deck is taught on which of those days, so by default the
whole bucket's decks form one assignment over the **date span** (e.g.
"Mon 2 – Tue 3 Mar: watch [decks]"). A trainer who wants to split the decks
across the individual dates uses an explicit `split` adjustment (§6.2).

Bare topics (a `<topic>` not inside any subsection) are build-only and never
appear in the schedule, so they never appear in the calendar either —
consistent with `export schedule`.

## 5. The date sequence (per cohort)

Generated lazily from the cohort calendar file:

```
start    : first teaching date
pattern  : which weekdays are teaching days
holidays : dates / intervals with no teaching
```

Algorithm:

```
cursor = start
repeat:
    if weekday(cursor) in pattern and cursor not covered by any holiday:
        yield cursor                      # a teaching date
    cursor += 1 day
```

`pattern` defaults to the set of weekdays actually used by the spec's
subsections (so a Mon/Tue/Wed part-time course needs no explicit pattern). It
can be overridden per cohort.

## 6. The cohort calendar file

One small, hand-editable file per channel, living beside the channel's ledger
(`release/jan.txt` → `release/jan.calendar.toml`). It is **not** part of the
spec XML — the spec stays diff-clean while this file absorbs the per-cohort
churn, exactly mirroring the ledger philosophy. **TOML**, because it is the
project's existing config idiom (`[tool.clm]`), parses with the stdlib
`tomllib` (no new dependency — a value the ledger module shares), has **native
date literals** (so `start`/`holidays`/`pin` dates become `datetime.date`
directly), and produces clean per-deviation diffs. The file is only ever *read*
programmatically (it is hand-edited; there is no `calendar add` writer), so
`tomllib`'s read-only nature is no limitation.

```toml
# Cohort "jan" of ml-course — viewing calendar
start = 2026-03-02              # first teaching date (a Monday)
end   = 2026-06-30              # last allowable teaching date; enforced by `check`
pattern = ["mon", "tue", "wed"] # optional; defaults to weekdays used in the spec

holidays = [
  2026-04-06,                                            # single day (Easter Monday)
  2026-05-01,                                            # single day (Labour Day)
  {from = 2026-07-20, to = 2026-08-02, label = "Summer break"},  # inclusive interval
]

# adjustments are an ordered array-of-tables — the only thing the trainer
# routinely touches. Order is the file order of the [[adjustments]] blocks.
[[adjustments]]
merge = 2026-03-18              # catch-up: collapse `count` buckets onto one date
count = 2

[[adjustments]]
split = "variables_intro"       # slow down one bucket across several dates
dates = [2026-03-25, 2026-03-26]

[[adjustments]]
insert = 2026-03-30             # a teaching date carrying no new video
label = "Review & Q&A"

[[adjustments]]
pin  = "control_flow"           # anchor a bucket to an exact date
date = 2026-04-09
```

Each `[[adjustments]]` table is identified by which of `merge` / `split` /
`insert` / `pin` key it carries (exactly one is required per table).

### 6.1 Holidays

Each entry is either:

- a **single date**: `2026-04-06`, or
- an **interval**: `{from = <date>, to = <date>, label = <text>?}`, **inclusive**
  on both ends. Intervals exist specifically for multi-day closures such as a
  two-week break — far less cumbersome than listing fourteen dates.

A holiday only removes dates that would otherwise be teaching dates (a holiday
falling on a non-teaching weekday is a harmless no-op, and `check` notes it).

### 6.2 Adjustments

An ordered list of perturbations applied to the default 1:1 zip, in sequence.

Each is a `[[adjustments]]` table carrying exactly one of these keys:

| Keys | Meaning |
|---|---|
| `merge = <date>`, `count = N` | The next `N` buckets all land on `<date>` (collapse to one date). Catch-up. Pulls all subsequent buckets earlier. |
| `split = <bucket-ref>`, `dates = [<date>, ...]` | The referenced bucket is spread across the listed dates. Decks may be distributed across the dates (optional per-date deck lists in a later iteration); by default the bucket's decks are shown on the first date and the rest are "continue". Pushes subsequent buckets later. |
| `insert = <date>`, `label = <text>` | A teaching date carrying no new video (review, exam, guest session). Consumes a date; pushes subsequent buckets later. |
| `pin = <bucket-ref>`, `date = <date>` | Anchor: the referenced bucket lands on exactly `<date>`. See §6.3. |

**Bucket references** (`<bucket-ref>`) are **topic or deck ids**, not week/weekday
coordinates. Topic ids are the stable identifiers already used by the ledger;
week/weekday coordinates shift whenever the spec is edited. `pin = "control_flow"`
means "the bucket containing the deck/topic `control_flow`". If a ref is
ambiguous or missing, `check` errors (§8).

A pin (or `split`) anchors the **whole bucket** that contains the referenced
deck, not the individual deck. Pinning a bucket's *first* deck and pinning its
*last* deck are identical operations — both say "this day's content lands on
this date". Within-day deck position is irrelevant to a pin; the deck id is just
the stable handle for naming the bucket. To move a single deck off its day and
leave the rest behind is a different, deliberate operation — a topic-level
`split` — never an emergent side effect.

### 6.3 Pins segment the timeline

A `pin` is both an **anchor** and a **firewall**. The timeline is divided into
segments at each pin (and at `start` / `end`). Each segment is projected
**independently**: the buckets within a segment are zipped, in order, onto the
segment's teaching dates, applying any `merge`/`split`/`insert` adjustments that
fall in that segment.

Consequences:

- An early miscount or a forgotten holiday cannot cascade past a pin — only its
  own segment is affected.
- The trainer self-corrects mid-course by pinning *the next bucket* to *today*:
  everything before is history, everything after re-projects from the anchor.
- A pin is the natural place to record a known fixed event: "after the two-week
  break we resume with `control_flow` on 2026-08-03".

### 6.3.1 The engine never guesses — fit is the trainer's call

Within a segment, buckets are placed **eagerly from the start anchor**, each
consuming `span` consecutive teaching dates, in plan order. The bucket carrying
the *end* pin must land exactly on the end pin's date. Three cases:

- **Exact fit** — buckets consume precisely the segment's teaching dates. Done.
- **Over-full** — more buckets (×span) than teaching dates between the anchors.
  The engine does **not** auto-distribute, auto-merge, or reflow at deck
  granularity (which would silently dissolve a day's editorial grouping).
  Instead `check` **errors** with the exact deficit (§8) — e.g. *"segment
  Tue Mar 3 – Fri Mar 6: 5 buckets, 4 teaching dates — merge ≥ 1 bucket"*. The
  trainer resolves it with an explicit `merge` (or `split` to push past the end
  pin), **choosing which days combine**. Result: one day's content combines
  where the trainer decided; every other day stays fixed.
- **Under-full** — fewer buckets than teaching dates. Buckets pack eagerly from
  the start anchor, leaving free teaching dates immediately before the end pin.
  `check` **warns** ("3 free teaching dates in segment …"); the trainer may
  `insert` a review/Q&A day, `split` a heavy bucket to fill, or leave the slack.

This is a deliberate **no-magic** rule: the bucket (a plan-day's decks) is the
atomic unit and its internal grouping is the trainer's editorial intent, never
silently regrouped. Catch-up always reads as an explicit, diff-visible `merge`;
the calendar stays predictable and reviewable.

### 6.4 Worked pin example (late start, hold the week's end)

Course intends a Mon–Fri week, `Mar 2–6`; five plan-day buckets:

| Bucket | Plan day | Decks |
|---|---|---|
| B_Mon | Mon Mar 2 | M1, M2 |
| B_Tue | Tue Mar 3 | T1 |
| B_Wed | Wed Mar 4 | W1, W2, W3 |
| B_Thu | Thu Mar 5 | H1 |
| B_Fri | Fri Mar 6 | F1, F2 |

The cohort started a day late, so the real teaching dates are **Tue, Wed, Thu,
Fri** (4 dates). The trainer wants Monday's content to begin Tuesday but still
finish the week on Friday:

```toml
[[adjustments]]
pin  = "M1"        # B_Mon starts Tue
date = 2026-03-03

[[adjustments]]
pin  = "F2"        # B_Fri lands Fri
date = 2026-03-06
```

Segment Tue–Fri now holds 5 buckets in 4 dates → `calendar check` errors:
`merge ≥ 1 bucket`. The trainer picks where to double up (say, the two lightest
adjacent days, Tue + Wed):

```toml
[[adjustments]]
pin  = "M1"
date = 2026-03-03

[[adjustments]]
pin  = "F2"
date = 2026-03-06

[[adjustments]]
merge = 2026-03-04   # B_Tue + B_Wed share Wednesday
count = 2
```

Result — predictable, and only the day the trainer chose is doubled:

| Date | Bucket(s) | Decks |
|---|---|---|
| Tue Mar 3 | B_Mon | M1, M2 |
| Wed Mar 4 | B_Tue + B_Wed | T1, W1, W2, W3 |
| Thu Mar 5 | B_Thu | H1 |
| Fri Mar 6 | B_Fri | F1, F2 |

## 7. CLI surface

```
clm export calendar <spec> --channel <name> [-f md|csv|ics] [-L de|en] [-o FILE | -d DIR]
clm calendar status  <spec> --channel <name>
clm calendar check   <spec> --channel <name>
```

- **`export calendar`** — render the projected calendar. Mirrors
  `export schedule` flags (`-L/--lang`, `-f/--format`, `-o/--output`,
  `-d/--output-dir`). Formats:
  - `md` / `csv` — trainer-facing views, same column conventions as
    `export schedule` plus a real-`date` column.
  - `ics` — the **student-facing payload** (see §8.1).
- **`calendar status`** — human summary: today's assignment, the next few days,
  and the **drift** vs the ideal plan ("plan Week 4 Tue → real Wed 6 May; +6
  teaching dates behind ideal"). The only "now"-relative command: it defaults to
  the **system date** and accepts **`--as-of DATE`** to override (for tests,
  dated handouts, and what-if previews). Its main operational value is catching
  slip: when the calendar's "today" bucket no longer matches where you actually
  are, that mismatch is the cue to add an adjustment.
- **`calendar check`** — validation only, non-zero exit on error (§8). Takes
  **no date**: whether the buckets fit between the anchors and before `end` is a
  pure function of the config, independent of when it runs. Suitable for a
  pre-push hook in the course repo.

The calendar attaches to an existing `<channel>`, so multiple cohorts/courses
are just independent `(spec, channel)` pairs — no shared state.

## 8. Validation (`calendar check`)

Errors (non-zero exit):

- `pin`/`split` bucket-ref is unknown or ambiguous against the spec.
- A segment is **over-full**: more buckets than available teaching dates between
  its bounding pins (or before `end`). Reported with the **deficit** —
  "segment Apr 9 – Jun 30: 22 buckets, 19 teaching dates; merge ≥ 3 buckets".
  This makes catch-up *quantified*, not eyeballed.
- The full content sequence does not fit before `end` — reports how many buckets
  must be merged to finish in time.
- A `pin` date is not a teaching date (wrong weekday or falls in a holiday).
- A `pin` date precedes its segment's start, or pins are out of order.

Warnings (exit 0):

- A holiday that falls on a non-teaching weekday (no-op).
- A pin-bounded segment is **under-full** (fewer buckets than teaching dates):
  buckets pack from the start anchor, leaving free dates before the end pin —
  "3 free teaching dates in segment Tue Mar 3 – Fri Mar 6".
- Calendar runs out of content before `end` (more dates than buckets — usually
  fine, the course just finishes early).

`check` itself is date-free. A separate, informational "this anchor date is in
the past" note belongs to `status` (which knows "today"), not to `check`.

### 8.1 `.ics` output

The `.ics` feed is what students subscribe to, so a pushed adjustment re-syncs
into their own calendar app automatically.

- One **VEVENT per assignment**, as an **all-day event** (`VALUE=DATE`).
- A span bucket (multi-weekday, §4.1) becomes a single multi-day all-day event
  (`DTSTART` = first date, `DTEND` = day after last date, per RFC 5545
  exclusive-end semantics).
- `SUMMARY` = localized video/deck titles for that assignment (`-L` controls
  language); `DESCRIPTION` lists deck files / topic ids for traceability.
- `UID` is stable across re-exports — derived from `(channel, bucket-ref)`,
  where the bucket-ref is each deck's globally-unique `module/topic/stem`
  identity (issue #436; *not* the bare slide-file stem, which collided when two
  decks shared it and silently dropped an event) — so re-issuing the feed
  *updates* events in place instead of duplicating them. This is what lets
  holidays/catch-up edits propagate cleanly to a subscribed student.
- `insert` entries (review/exam days) become events with no deck list.

## 9. Relationship to release (kept independent)

This design deliberately does **not** drive solution release. Release stays the
separate, manual ledger-based flow. The two layers share a conceptual spine —
both are per-channel, both live as small hand-editable files beside each other
in the course repo — and a future iteration *could* let a dated-release policy
read the same projection (e.g. "release a topic's solutions *D* teaching-days
after its viewing date"). The ledger format already anticipates this with its
`topic_id @ YYYY-MM-DD` comment. For now they are decoupled: the calendar tells
students what to watch by when; the trainer still decides explicitly when to
unlock solutions.

## 10. Data model (sketch)

```python
@define
class Bucket:
    decks: list[ScheduleDeck]   # reuse export-schedule's ScheduleDeck
    span: int                   # consecutive teaching dates this bucket occupies
    week: int                   # plan-relative, for drift reporting / labels
    weekday_label: str          # plan-relative label

@define
class CohortCalendarConfig:     # parsed from release/<channel>.calendar.toml
    start: date
    end: date | None
    pattern: tuple[str, ...]    # weekday tokens; empty → derive from spec
    holidays: tuple[Holiday, ...]   # Holiday = single date | inclusive interval
    adjustments: tuple[Adjustment, ...]   # Merge | Split | Insert | Pin, in file order

@define
class Assignment:               # one calendar row
    start_date: date
    end_date: date              # == start_date unless a span / split
    decks: list[ScheduleDeck]   # empty for an `insert`
    label: str | None           # for inserts; else None
    bucket_ref: str | None      # stable UID seed for .ics

def project(buckets, config) -> list[Assignment]: ...
```

- The content sequence (`buckets`) comes from refactoring `export schedule`'s
  builder to expose buckets, so the two commands cannot drift apart.
- `project` generates teaching dates, segments at pins, zips per segment, and
  applies in-segment adjustments — pure and unit-testable with no I/O.

## 11. Worked example (the current real cohort)

Course planned as 12 weeks × Mon/Tue/Wed. This cohort started a few days late and
hit two single-day public holidays plus a two-week break, and is now ~1 week
behind. The trainer wants to recover one day.

```toml
# release/spring.calendar.toml
start = 2026-03-04        # started Wed, not Mon — two slots simply don't exist
end   = 2026-06-17
pattern = ["mon", "tue", "wed"]
holidays = [
  2026-04-06,                                    # Easter Monday
  2026-05-01,                                    # Labour Day
  {from = 2026-05-18, to = 2026-05-29, label = "Two-week break"},
]

[[adjustments]]
merge = 2026-06-09        # double up one day to recover
count = 2
```

- The late start, two holidays, and the break all drop teaching dates; content
  slides later automatically.
- `calendar check` confirms the 12 weeks of buckets still fit before
  `2026-06-17` *given* the one `merge`; without it, `check` would report the
  deficit ("merge ≥ 1 bucket to finish by Jun 17").
- `export calendar spring.calendar.toml -f ics` gives students a feed that, after
  the next push, shows the recovered day and the shifted dates with no duplicate
  events.

## 12. Open questions / future work

- **Per-date deck distribution in `split`** — *deferred.* First iteration shows
  the bucket's decks on the first date; richer per-date deck lists can come later.
- **Dated release policy** — the §9 coupling, if pilots want it.
- **Validation in CI** — wiring `calendar check` into the course repo's hooks.
- **Google Calendar push** — *shipped* as `clm calendar push`
  (`clm.cohort_calendar.google_sync`, `[gcal]` extra): a one-way mirror into a
  shared Google calendar, diffing against CLM-managed events tagged with the
  cohort namespace plus the §8.1 stable UIDs (private extended properties), so
  re-pushes update in place and foreign events are never touched. Target id
  from `--calendar-id` or the `[google]` table in the calendar TOML.

**Decided:** `check` and `export calendar` are pure functions of the config and
take no date. `status` is the only "now"-relative command — it defaults to the
system date and accepts `--as-of DATE` (for deterministic tests, dated handouts,
and what-if previews).

## 13. Implementation phases (proposed)

1. **Content sequence** — refactor `export schedule` to expose ordered `Bucket`s
   (incl. `span`); no behavior change to `schedule`.
2. **Config + parse** — `release/<channel>.calendar.toml` schema, loader,
   `Holiday` interval support, defaulted `pattern`.
3. **Projection engine** — date generation, pin segmentation, adjustments; pure
   and unit-tested.
4. **`export calendar`** — `md`/`csv`/`ics` renderers (reuse schedule columns;
   stable `.ics` UIDs).
5. **`calendar check` + `calendar status`** — validation and drift reporting.
6. **Docs** — `clm info` topics (`commands.md`; a calendar section in
   `spec-files.md` / a new file-format note), user-guide page, CHANGELOG.
