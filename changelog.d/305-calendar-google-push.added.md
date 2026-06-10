- **`clm calendar push` — mirror a cohort's viewing calendar into Google
  Calendar.** Students subscribe to one shared Google calendar and pushed
  schedule changes propagate within minutes (no `.ics` hosting, no feed-refresh
  lag). The push only touches CLM-managed events: each event is tagged (private
  extended properties) with the cohort namespace and the same stable
  per-assignment UID the `.ics` export uses, so re-pushing updates events in
  place, deletes vanished ones, and never disturbs other events in the same
  calendar. Credentials (`--credentials` / `CLM_GOOGLE_CREDENTIALS`) accept an
  OAuth "Desktop app" client (one-time browser consent, cached token) or a
  service-account key, auto-detected; the target comes from `--calendar-id` or
  a new optional `[google] calendar_id` table in the cohort calendar TOML.
  `--dry-run` previews the insert/update/delete plan. Requires the new `[gcal]`
  extra (`google-api-python-client`, `google-auth`, `google-auth-oauthlib`).
