- `clm build`: cache-hit partial HTML renders blanked in-workshop `answer`
  markdown as the localized `*Answer:*` / `*Antwort:*` stub, matching a
  fresh partial build (#737) — previously the cached path emitted an empty
  cell where the fresh path showed the stub.
