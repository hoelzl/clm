- The provenance manifest now lists the root files (`include-root-files`) of a
  dir-group that has `<subdirs>` or `recursive="false"`, so `clm release`
  ships them as skeleton. Before, they were never walked and reached a
  manifest only when another dir-group happened to cover the whole output
  root (#1288).
