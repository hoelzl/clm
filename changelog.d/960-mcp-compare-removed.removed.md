- **Removed the MCP tool `harvest_compare`** (#960, umbrella #970): it was
  the only MCP tool that invoked a model (the bullet-relation judge), which
  violated the read-only, model-free mirror contract. The replacement is the
  extended task mirror: `harvest_task` accepts `kind="port"` /
  `kind="compare"` with a `source` argument (no videos), framing the same
  tasks the CLI's `clm harvest task --kind port|compare --source FILE`
  emits. See `clm info migration`.
