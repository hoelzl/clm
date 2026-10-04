- **`clm build` works inside the notebook worker image.** The clm installed in
  the notebook worker image lacked the `clm.build` package, because
  `.dockerignore`'s `**/build` (meant for build artifacts) also matched
  `src/clm/build/`. Running `clm build` inside the image failed with
  `ModuleNotFoundError: No module named 'clm.build'`. The package is now
  re-included, mirroring the `!src/clm/build/` exception that `.gitignore`
  already had.
