- `clm serve` Studio: the tier-2 cell preview's Jinja expansion runs in a
  **killable subprocess under a wall-clock budget** (#698). The in-process
  sandbox and value caps bound memory but could not bound CPU — a nested
  `range()` loop burned hours producing two characters, and the old
  worker-thread route let 40 slow renders occupy the shared threadpool that
  also serves the `/studio/` app shell. A timed-out render now degrades to
  the tier-1 client-side fallback like every other preview failure, the
  route holds no threadpool token while waiting, and the child carries a
  POSIX address-space rlimit as a belt. Reachable only by a Studio
  bearer-token holder (the trust boundary, decision D4) — defense in depth,
  not a new exposure.
