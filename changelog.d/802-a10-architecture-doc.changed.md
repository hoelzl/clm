- Rewrote `docs/developer-guide/architecture.md` to describe the architecture
  that actually exists after the #802 re-layering: the enforced
  `core ← infrastructure ← workers` stack with the CLI and extension packages
  as unconstrained consumers on top, the contract seam in `clm.core`, the
  import-linter enforcement story, honest build-orchestration attribution,
  the WAL/network journal-mode policy, and a "Known Deviations and Pending
  Work" list for the remaining #802 items. The previous revision described a
  fictional layering (adversarial-review finding A10).
