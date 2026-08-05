- **`scripts/curate_test_course.py` + `scripts/test_course_manifest.json` —
  the #682 public-corpus regeneration path.** Manifest-driven selection over
  the course repos, deterministic structure-preserving sanitization (headers,
  ids, tags and byte-equality relations preserved; prose replaced per
  language), synthetic decks for the refusal shapes the live corpora no
  longer carry, and a per-deck parity verifier (member keys,
  langness/layout/kind/role, observations, refusal codes). The staged corpus
  is reviewed by the maintainer before publication; CLM's gates retarget to
  the published repo in a follow-up.
