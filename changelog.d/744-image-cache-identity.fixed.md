- `clm build`: the caches now key on the **effective worker image** for all
  three worker types (#744). Diagram (PlantUML/Draw.io) results previously
  cached on the source bytes alone — a rebuilt converter image silently
  replayed the old image's output for every unchanged diagram; their cache
  keys now fold in the worker-image identity, the output format, and a
  schema version (one-time diagram re-render on upgrade). The notebook
  image identity now sees CLI overrides too: `--notebook-image X` used to
  execute on `X` but key the cache as the configured default, replaying
  stale outputs — `clm build` records the post-override identities and
  payload construction reads them. Residual limits: mutable tags
  (`:latest`) re-pulled to a new image do not change keys, and worker
  reuse remains image-blind.
