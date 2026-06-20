- **Mobile Deck Studio P3b — sync-to-other-language.** The phone authoring
  surface (`clm serve --spec`) can now propagate edits between a split DE/EN
  pair: a "Sync languages" action runs `clm slides sync` as a server-side
  subprocess and streams its progress to the phone over the WebSocket, then
  reloads the deck with the freshly reconciled content and the released language
  lock. Concurrent syncs of the same pair are rejected (409). (#395)
