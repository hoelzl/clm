"""The sync verbs' wire contract version — one number, both directions.

``report``'s envelope and the decision documents ``apply --decisions`` reads
are **one** contract with **one** version: an agent copies handles (and, since
schema 4, the freshness token) straight out of a report into its answers, so a
report envelope and a decision document that disagree about the schema are a
contradiction in terms. Every payload the sync verbs emit therefore carries
:data:`WIRE_SCHEMA`, and every document they read is checked against
:data:`ACCEPTED_DECISION_SCHEMAS`.

Schema 4 (Q2/Q3 of ``docs/claude/sync-v3-adversarial-review.md``) is additive:

* ``report_id`` — a freshness token over the bundle bytes plus this deck's
  ledger section, echoed back in a decision document so ``apply`` can refuse a
  document written against a report that no longer describes the deck. Before
  it existed, "stale handle" named a report that did not exist at apply time
  and the verdict could contradict the effect (#649).
* ``already_applied`` — a decision whose effect already holds is no longer
  reported as ``rejected``.
* ``deck_key`` / ``ledger`` — the deck's ledger identity, in every payload, so
  two CLI spellings of one deck are visibly one deck.

Schema 5 (#773 phase 1) is additive, on the **report** side only: framed
``verify_translation`` / ``translate_edit`` items may carry ``base_ref`` plus
per-side ``de_diff`` / ``en_diff`` — unified diffs against the newest commit
whose bytes the ledger fingerprints recognize (:mod:`clm.slides.base_recovery`)
— and a deck whose ``verify_translation`` rows all share one recovered base
emits a ``verify_translation_batch`` observation. The fields are optional
(recovery degrades to absence); the decision-document shape is unchanged, so
schema-4 documents remain first-class input, not a compatibility case.

Rollout: a decision document with **no** ``report_id`` is accepted with a
warning naming the field (agents and downstream sweep drivers keep working);
one whose token does not match is refused wholesale — exit 2, nothing written.
The maintainer's decision (2026-07-31) is that the token-less form is accepted
for one release and rejected in the next; that tightening flips
:data:`REQUIRE_REPORT_ID`.
"""

from __future__ import annotations

__all__ = [
    "ACCEPTED_DECISION_SCHEMAS",
    "REQUIRE_REPORT_ID",
    "WIRE_SCHEMA",
]

#: The version every report / apply / record payload announces.
WIRE_SCHEMA = 5

#: Decision-document schemas ``apply`` still reads. Schema 3 documents carry no
#: ``report_id`` and no ``action`` discriminator; they remain valid input for
#: one release so that a clm upgrade does not break a driver mid-flight.
#: Schema 4 and 5 decision documents are byte-identical (5 is report-side
#: additive), so both stay accepted indefinitely.
ACCEPTED_DECISION_SCHEMAS = (3, 4, 5)

#: Flip to ``True`` in the release that drops schema 3 (see the module doc).
REQUIRE_REPORT_ID = False
