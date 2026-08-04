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

Rollout, completed: schema 4 shipped in 1.24.0 with a one-release grace — a
decision document with no ``report_id`` was accepted with a warning naming the
field, so drivers kept working mid-upgrade. Per the maintainer's decision
(2026-07-31, executed for the release after 1.24.0), the grace has ended:
:data:`REQUIRE_REPORT_ID` is ``True`` and schema-3 documents are refused, so
every decision document now carries the freshness token and a stale one is
refused wholesale — exit 2, nothing written.
"""

from __future__ import annotations

__all__ = [
    "ACCEPTED_DECISION_SCHEMAS",
    "REQUIRE_REPORT_ID",
    "WIRE_SCHEMA",
]

#: The version every report / apply / record payload announces.
WIRE_SCHEMA = 5

#: Decision-document schemas ``apply`` reads. Schema 3 (no ``report_id``, no
#: ``action`` discriminator) had its one-release grace in 1.24.0 and is now
#: refused. Schema 4 and 5 decision documents are byte-identical (5 is
#: report-side additive), so both stay accepted indefinitely.
ACCEPTED_DECISION_SCHEMAS = (4, 5)

#: The Q2 tightening, flipped in the release after 1.24.0 (see the module doc):
#: a decision document must carry the ``report_id`` it answers.
REQUIRE_REPORT_ID = True
