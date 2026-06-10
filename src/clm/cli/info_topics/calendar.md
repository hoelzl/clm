# CLM {version} — Cohort Calendar Reference

A cohort calendar projects a course's content schedule onto the real teaching
dates of a specific cohort. Each cohort has its own `.calendar.toml` file that
records the start date, teaching-day pattern, holidays, and any adjustments.

## Commands

```
clm export calendar <spec>   # Render the cohort schedule (md/csv/ics)
clm calendar check  <spec>   # Validate the calendar file; non-zero on errors
clm calendar status <spec>   # Show today's position vs plan
```

### Common flags (all three commands)

| Flag | Description |
|---|---|
| `--channel NAME` | Cohort channel name; resolves `release/<NAME>.calendar.toml` beside the channel ledger |
| `--calendar PATH` | Explicit path to the `.calendar.toml` file (overrides `--channel`) |
| `--data-dir PATH` | Course data directory containing `slides/` (default: auto-detected) |

Exactly one of `--channel` or `--calendar` is required.

### `clm export calendar` flags

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
```

### Weekday tokens

`mon`, `tue`, `wed`, `thu`, `fri`, `sat`, `sun` (lowercase, three-letter).

### Bucket references (for `pin` and `split`)

A bucket reference is a **topic ID** or **deck-file stem** (e.g.
`slides_010_introduction_ml`). These are stable identifiers that do not change
when the spec is reordered; use them instead of week/weekday coordinates.
Unknown or ambiguous references are reported as errors by `clm calendar check`.

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

## Examples

```bash
# German Markdown to stdout
clm export calendar course.xml --channel jan

# English iCalendar file for student subscriptions
clm export calendar course.xml --channel jan -f ics -L en -o jan.ics

# CSV for spreadsheet import
clm export calendar course.xml --calendar release/jan.calendar.toml -L en -f csv

# Validate before pushing
clm calendar check course.xml --channel jan

# Show where the cohort is today
clm calendar status course.xml --channel jan -L en

# Check status as of a specific date
clm calendar status course.xml --channel jan --as-of 2026-04-15
```
