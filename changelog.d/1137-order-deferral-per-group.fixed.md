- `clm slides sync apply`: a placement or order question in one slide group no
  longer defers a mechanical `mirror_order` in another. The "one order
  authority" rule (#885, #906) is now per slide group, and stays deck-wide when
  a pass has an order row that can reach another group (`~groups`, a companion scope, an `id:`
  cross-group move, `mirror_pool_move`) or a group with a cell outside its own
  bracket. When the only deck-wide verify finding is the `order-parity`
  divergence left by a mirror deferred under that rule, answered `body` rows on
  id'd cells still record, so an agent does not re-confirm text it just wrote
  as `verify_cold` (#1137).
