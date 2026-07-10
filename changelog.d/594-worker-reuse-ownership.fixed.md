- Fixed intermittent `RuntimeError: No workers available to process 'notebook' jobs`
  mid-build (issue #594). Worker reuse could count another build's auto-stopped
  workers in a shared jobs DB as reusable; when that build exited, its workers were
  deleted and the borrowing build was stranded with zero workers at its first
  non-cached submission. Managed workers are now stamped with their owning session
  (`workers.session_id`) and an ownership marker (`workers.managed_by`): a build
  only reuses its own workers, deliberately persistent workers (`auto_stop=false`),
  or unmarked externally started workers.
