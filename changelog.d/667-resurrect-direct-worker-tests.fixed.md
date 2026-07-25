- **Eight Direct-mode worker integration tests run again.** `test_direct_integration.py`
  gated its entire module on `find_spec("drawio_converter")` and
  `find_spec("plantuml_converter")` — top-level module names that stopped
  existing when the workers were folded into `clm.workers.*`, so worker
  startup/registration, concurrent claiming, health monitoring, graceful
  shutdown and the high-concurrency job tests had been silently skipped
  everywhere, including CI. The probes now name the real packages. Resurrecting
  them exposed a stale job payload in two of the tests (`{"kernel": …}`
  predates `NotebookPayload`'s required descriptor fields, so the job always
  failed validation); they now submit a real `NotebookPayload`. A new
  `test_worker_module_probes.py` fails loudly if a skip guard is ever pointed at
  a module that does not resolve.
