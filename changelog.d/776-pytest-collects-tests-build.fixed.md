- **`tests/build/` is collected again.** Pytest's default `norecursedirs`
  contains `build`, so the `tests/build/` test package (split-source build
  routing, shared-cell parity messages) was silently never collected by a bare
  `pytest` — neither the pre-push hook nor any CI job ran it. `pyproject.toml`
  now pins `norecursedirs` to the pytest default minus `build`; the 14
  previously-invisible tests pass unchanged. (#776)
