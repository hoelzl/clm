- **`clm release provision --dry-run` reports credential status, and the
  command reads the project `.env` (#870).** The preview passed with no
  token configured and the real run then failed at once, so it validated
  channel/group resolution but was silent about the half most likely to be
  wrong. It now ends with a `credentials:` line naming the token variable
  found, or `MISSING — the real run will fail` (exit code still 0). And,
  matching `clm build`, the project's `.env` (found by walking up from the
  spec file) is loaded first, so a `CLM_GITLAB_TOKEN` kept there counts; an
  exported value wins.
