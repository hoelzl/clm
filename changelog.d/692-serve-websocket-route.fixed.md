- **`clm serve`'s `/ws` endpoint never worked.** Its route function took an
  unannotated `websocket` parameter, so FastAPI analysed it as a required
  *query* parameter and closed every handshake with
  `{"loc": ["query", "websocket"], "msg": "Field required"}`. The Mobile Deck
  Studio's "changed on disk — reload" banner and its live sync progress line
  therefore never fired; the deck simply went stale without saying so. Found
  while adding the token check to the same endpoint.
