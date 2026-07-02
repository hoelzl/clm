- **Sync v3 Phase 1 — canonical bilingual document model + lens projections** (#520):
  new `clm.slides.bilingual_doc` (the `BilingualDeck` model with total `MemberKey`
  identity per design §3.3) and `clm.slides.doc_lenses` (`parse_bundle` reads the
  ≤4-file deck+companion bundle into one document; `project` renders each file back
  byte-identically). Projection mismatches (one-sided members, shared divergence,
  the #443 id'd-on-one-half shape, layout mixtures) are recorded as first-class
  observations; input failing the §3.4 normalize precondition (duplicate ids,
  id-less anchors, id-less localized/narrative cells) yields a framed
  "normalize first" refusal with every reason enumerated. Round-trip laws are
  pinned by golden + Hypothesis property tests plus a corpus gate
  (`tests/data/doc_corpus` bundled fixtures in CI; the full-corpus
  `project ∘ parse` byte-identity run verified locally over PythonCourses:
  644/706 pairs parse — the 62 refusals are the standing normalize worklist —
  with zero byte divergences). Internal-only for now: no CLI surface change; the
  v2 engine is untouched (an import-cleanliness test pins that the v3 modules
  never import `sync_plan`/`sync_apply`/`sync_code`, per the §12.5 cutover design).
