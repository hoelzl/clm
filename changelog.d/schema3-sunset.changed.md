- **Sync decision documents must now carry `report_id`; wire schema 3 is
  retired.** `clm slides sync apply --decisions` refuses a document that omits
  the freshness token or announces `"schema": 3` — exit 2, nothing written,
  with the refusal naming the field and the accepted schemas ({4, 5}). This
  executes the tightening announced with wire schema 4 (1.24.0), whose
  token-less grace lasted exactly one release. Drivers copy `report_id` out of
  the `report --json` envelope; see `clm info sync-agents` and the migration
  guide.
