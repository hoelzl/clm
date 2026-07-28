- `clm slides sync`: the pairing lens no longer adopts an id'd-on-one-half
  cell as a positional twin when the id'd side's pool holds surplus cells
  (#716). Previously a newly inserted id'd cell that was byte-identical to an
  un-id'd neighbor (or carried a `lang` attribute) could steal that
  neighbor's twin — making `apply` mechanically delete the authored cell on
  the other half, or handing a new localized cell another slide's
  translation. Under a surplus the cell now stays one-sided and frames
  normally (`copy_new_shared` / `translate_new`).
