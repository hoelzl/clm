- `clm slides sync`: the structural checks now detect **order-parity**
  divergence — the two halves ordering their common id'd cells differently
  (a group swap, a one-sided slide move). `sync verify` reports it as a
  warning; the ledger write gate treats it as blocking, so `record` and
  `apply`'s ledger save refuse to certify an order-divergent pair (#719).
  Previously a swap whose moved region held only localized cells passed the
  gate and the ledger recorded the corrupt pair as verified (#652).
