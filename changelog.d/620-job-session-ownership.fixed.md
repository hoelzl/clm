- Fixed a killed or concurrent build poisoning another build's jobs (issue
  #620). Jobs in the shared jobs DB carried no session ownership, so a worker
  would claim any pending same-type/same-mode job — including one submitted by a
  different build whose workspace it cannot address — and fail it in Docker mode
  with `... is not in the subpath of ...`, misattributed to an innocent slide
  file. Jobs are now stamped with their owning build session (`jobs.session_id`,
  mirroring the `workers.session_id` ownership added in #594): a worker claims
  only its own session's jobs plus unowned (`NULL`) legacy jobs, and a worker
  with no session stays unrestricted so a build can never deadlock on its own
  jobs.
