- **Narrowing `-T` no longer executes `html="no"` decks (#871).** A build whose
  target set lacks the `recording` producer schedules an *implicit*
  Recording-HTML execution per notebook to warm the executed-notebook cache
  for the consumer HTML it did request. That block ignored the topic's
  `html="no"` flag, while the explicit output list honours it — so a deck
  with no HTML outputs at all, which never runs in a full build and therefore
  has no HTTP-replay cassette by construction, was executed as soon as the
  target list was narrowed (`-T shared`), and its unmatched requests went to
  the network live. The failure then surfaced as a deterministic
  `[User Error]` on a deck that had "been building fine". Implicit
  cache-producer executions now skip `html="no"` topics; the deck behaves the
  same under every `-T` selection and still produces its non-HTML outputs.
