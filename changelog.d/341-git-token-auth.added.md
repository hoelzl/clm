`clm git` and `clm release sync --push` can now authenticate HTTPS git
operations with the GitLab token (#341): set `CLM_GIT_TOKEN_AUTH=1`
together with `CLM_GITLAB_TOKEN` (or `GITLAB_TOKEN`) and every git network
operation (push, fetch, ls-remote, clone) uses an ephemeral credential
helper with `oauth2:<token>` basic auth — enabling unattended pushes from
CI, cron, and containers where no credential helper exists. The token
never appears in the URL, in `.git/config`, or on the command line, and
the switch is opt-in so workstations keep using their stored credentials
by default.
