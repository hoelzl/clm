- **Sync**: two defects found by the adversarial review of the `stamp_vs_new`
  action (#600, PR #602). The vanished-cell (`pos:…`) row is no longer emitted
  when the surviving twin was also edited — its only advertised answer
  (`treat_as_new` → mirrored removal) was deterministically rejected by the
  off-base guard, an unbreakable report→answer→reject loop; the shape now
  frames `remove_vs_edit` (whose `remove`/`keep` answers land) with the stamp
  suspicion in the row's detail. And answering `treat_as_new` on only *some*
  rows of a pool no longer erases the ledger evidence behind the pool's other
  suspected-stamp rows: pools that still carry an unresolved
  `stamp_vs_new`/`remove_vs_edit` item are frozen during apply's ledger
  re-record, so the remaining suspicion keeps its framing on the next report
  instead of silently downgrading to mechanical cell duplication.
