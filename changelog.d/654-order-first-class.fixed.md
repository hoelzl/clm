- `clm slides sync`: order divergence is now detected from the **current**
  cross-side state, whether or not the ledger carries recorded order trust
  (#654). Previously order items could only frame against recorded order
  scopes, which only a full `record`/`split`/`translate-bootstrap` ever
  seeded — a ledger built through report → confirm → apply was permanently
  order-blind: a one-sided slide move framed nothing, `apply` left the twin
  stale, and the follow-up report said `is_clean` while `clm validate`
  flagged the divergence. Now: with recorded trust a one-sided move stays a
  mechanical `mirror_order`; without it (cold decks included) the divergence
  frames an `order_decision` naming both sequences; a same-pass rename+edit
  of a slide no longer destroys the evidence; a fully-resolved `apply` pass
  seeds order trust for the scopes whose sides agree, so the mechanical
  path becomes available after the first clean pass; and a parse-observed
  group-order divergence suppresses `is_clean` outright.
