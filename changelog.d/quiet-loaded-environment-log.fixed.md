- **Quiet stray "Loaded environment from …" line at the start of `clm build`.** The
  dotenv-loading step ran before `setup_logging` replaced the bootstrap
  `basicConfig` console handler, so its `INFO` log leaked to the terminal even
  with console logging off. Demoted to `DEBUG` so the build starts cleanly.
