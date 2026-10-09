---
status: active
owner: maintainers
updated: 2026-10-09
review-by: 2027-04-09
---

# cam-portal inception — state

**Question.** CAM now runs many concurrent courses with different trainers and
a back-office. Should clm grow into a single multi-module system, or should
role-specific systems share a library?

**Answer (S13).** Two systems plus a contract.
- **clm** stays the public authoring and build tool. Its new job is to emit
  versioned, CI-built **release bundles**.
- A new private system, **cam-portal**, is the system of record for course
  operations: people, runs, enrollments, roles, schedules, releases from pinned
  bundles, attendance, certificates, and provisioning of GitLab, Memberspot and
  Google.

The inception document is the canonical outcome:
`hoelzl/cam-portal` → `docs/inception.md` (private repo, commit `288c816` plus
the Q1 fix in the same commit). This file holds only the conversational state.

## Settled (owner's answers, S13)

- **Scale:** medium (5–20 concurrent courses or trainers, ≤1–2k participants
  a year). Long AZAV cohorts **and** short workshops. The same course runs in
  parallel, with several trainers per run.
- **Users:** trainers work via CLI/git; the back-office needs a GUI.
  Participants never touch the system (only Memberspot, GitLab, email).
- **Participant data:** there is no system of record today. Memberspot and
  GitLab accounts are scattered, and Close CRM is marketing-only (integration
  "nice to have later").
- **Trainer permission unit:** the whole run. **Trainers get speaker `notes`
  but no `voiceover`.** That is exactly clm's existing `trainer` kind. The
  owner corrected the first phrasing, "without the speaker notes".
- **Releases:** a central CI build, so trainers never need sources. A run pins
  a course version and trainers opt in to updates.
- **V1 pain points:** onboarding a run, releasing, AZAV paperwork (attendance +
  certificates only), mid-run changes. Schedule shifts are frequent *now*.
- **Memberspot:** recorded videos, access by group, a recording upload
  pipeline.
- **GitLab:** trainers and participants both have accounts, so it is the
  candidate OIDC IdP.
- **Infrastructure:** EU hosting, CAM's own server, AZAV audit retention. No
  hard deadline. Built by the owner plus AI agents.
- **Architecture:** split by **data/trust boundary, not by role**. A permission
  matrix needs one enforcing server, and participant data must not go into
  the public clm package.
  - cam-portal is a modular monolith (server + web UI + thin `cam` trainer CLI)
    in one uv-workspace repo.
  - The shared contract is the bundle format plus the release semantics
    extracted from `clm.release`.
- **Repo:** private GitHub, `hoelzl/cam-portal`.
  `Coding-Academy-Munich` (the old `cam-tools` org) is noted as a possible
  later home.

## Open (tracked as Q2–Q11 in the inception doc §10)

- IdP details: are back-office staff GitLab users? How are participant
  accounts linked?
- Memberspot API capabilities (gates P4).
- Migrating running cohorts' clm ledgers.
- Bundle store and size.
- CI build cost (notebook execution, possibly a self-hosted runner).
- Certificate legal format.
- Ops burden.
- A lighter path for workshops.
- Participant messaging.

## Next boundary

The owner reviews `docs/inception.md`. The next conversation goes through the
open questions and then designs **P0**, the clm side: bundle format spec,
`clm bundle build`, CI publishing, and refactoring `clm.release` onto bundles.
P0 work happens in this repo and is the first clm-side change. The rest lives
in cam-portal.

## Known weak points of the record

- The S13 cleaned transcript lacks two agent messages: the code-exploration
  summary and the "split by data and trust boundary" argument before the
  architecture question. The raw session file has only their reasoning traces,
  not the public text. The argument is written out in inception §6. The
  owner's choice is in the transcript's AskUserQuestion stubs.
