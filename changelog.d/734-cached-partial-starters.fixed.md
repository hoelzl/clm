- `clm build`: cache-hit partial HTML no longer loses workshop starter
  cells (#734). Recording deleted `start` cells before caching its executed
  notebook, so the cached-partial path — unlike a fresh partial build —
  emitted workshops without their scaffolding. The cached artifact now
  retains the starters **unexecuted** (they are often deliberately
  incomplete and are skipped at execution); the cached-partial view keeps
  them in-range exactly like a fresh build, and every other view
  (Recording's own HTML, completed/trainer-from-cache, pre-workshop
  partial) drops them at its boundary as before. The notebook cache schema
  version was bumped again — the first build after upgrading re-executes
  once instead of replaying starter-less artifacts.
