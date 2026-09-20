# The Agent-Task Contract (CLM {version})

CLM's agent toolkits (`clm slides sync`, `clm harvest`, `clm slides
translate`, `clm slides polish`, `clm slides coverage` (with `assign-ids
accept`), and the conversions tracked in umbrella issue #970) share one
contract. This topic is its single canonical description; the per-toolkit
guides (`clm info sync-agents`, `clm info harvest-agents`) describe each
toolkit's verbs and payloads and point here for the shared rules. The
machinery lives in `clm.slides.agent_task` (the shared kit, #959).

## Principles

- **Read by default.** The bare command reports; every write is an explicit
  verb. A read verb never mutates the deck, the ledger, or the cache's
  semantic content.
- **Emit, don't invoke.** Judgement is framed as a JSON task — caller
  `instructions`, structured `inputs`, an `answer_schema`, and freshness
  tokens — for the driving agent (you) to answer. The engine validates the
  answer's shape and freshness only, never its quality. Embedded models
  survive only behind `autopilot`, for agent-less humans; never in CI.
- **Freshness tokens.** A task frames tokens over the exact state it
  describes (a `report_id`, per-member `baseline_fingerprints`, a
  `video_fingerprint`, …). Echo them **verbatim** in your answer. The
  accept path re-computes them against the live state and refuses a stale
  answer wholesale — a concurrent edit is re-judged, never merged. A
  mismatch in the *member set* (a cell added or removed since the task was
  framed) is staleness too.
- **Member handles.** Everything is keyed by stable handles
  (`id:<slide_id>`, `pos:<group>/<kind>/<n>`). Never address content by
  position you counted yourself.
- **Validator registry.** A task document names its answer validator (e.g.
  `"validator": "harvest-bullets"`); the accept path resolves that label
  through the kit's registry to the parser that judges your answer. The
  label is a fact, not a hint: unknown or mismatched shapes are rejected
  with precise reasons.
- **Ledger as trust store.** Verified state is banked in a committed ledger
  with provenance (`agent`, `record`, `harvest:<video-fingerprint>`,
  `semantic:<model>`). Writes are atomic; a structural gate can withhold
  the ledger record while leaving the files written (fail-safe).
- **Load-bearing exit codes.** `0` clean / applied · `1` work pending /
  applied with a withheld follow-up · `2` error / rejected (nothing
  written). Script against them, not against prose.
- **Guide + mirror.** Every toolkit has an `<x>-agents` info topic and a
  read-only MCP mirror of its read verbs. Writes stay CLI-only.

## Envelope shape

Every payload a toolkit emits opens with an ordered identity head, then the
verb's body:

```json
{"schema": <int>, "tool"|"engine": <name>, "verb": <name>, ...}
```

Key order is part of the readable contract. `schema` versions the whole
contract (a toolkit may accept more than one decision-document schema; an
unknown schema is an error, not a field to ignore). Rejection envelopes
carry the same identity head as a success — a refusal is always
distinguishable from a crash by a JSON consumer.

## The canonical loop

```
report            → items with their decision vocabulary / task framing
task (if present) → one framed judgement: instructions + inputs +
                    answer_schema + freshness tokens
… you judge …
accept / apply    → validated writes; exit 0/1/2 as above
verify            → structural post-check
record            → bank the verified state in the ledger
```

Per-toolkit specifics: `clm info sync-agents`, `clm info harvest-agents`.
