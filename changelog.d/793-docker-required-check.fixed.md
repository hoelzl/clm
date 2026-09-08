- The Docker-job promotion tracker (`scripts/docker_job_stability.py`) now
  checks the branch ruleset first and retires itself once
  `Docker Integration Tests` is a required status check, closing its tracking
  issue instead of refreshing it. Previously it only ever looked for an *open*
  issue, so after the job was promoted and #679 closed, the nightly opened a
  fresh issue the next morning and spent a month asking for a decision that had
  already been taken (#793). The rules lookup fails open: an unreadable ruleset
  means "not yet promoted", never a spurious close.
