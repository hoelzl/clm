- CLI startup is ~4x faster: `clm.cli.main` now loads command modules lazily
  (`LazyGroup`), and the `clm`/`clm.core`/`clm.infrastructure` package inits
  resolve their convenience exports via PEP 562 instead of importing the whole
  core/infrastructure stack on every invocation. `from clm import Course` and
  `from clm.cli.main import BuildConfig`-style imports keep working unchanged.
