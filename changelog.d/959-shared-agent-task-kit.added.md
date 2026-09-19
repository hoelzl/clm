- Extracted the agent-toolkit contract shared by `clm slides sync` and `clm
  harvest` into one kit (#959, umbrella #970): `clm.slides.agent_task` owns
  the envelope builder, freshness-token check, validator registry
  (`harvest-bullets`, `sync-decisions`), accept-rejection payload, and the
  0/1/2 exit-code constants; `clm.cli._default_verb_group` owns the two
  bare-command-is-`report` Click groups. New `clm info agent-tasks` topic
  documents the contract and is referenced from both `sync-agents` and
  `harvest-agents`. No wire change: both toolkits' JSON contracts are
  byte-identical.
