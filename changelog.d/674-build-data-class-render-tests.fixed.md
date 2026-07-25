- **The `BuildError` / `BuildWarning` tests now assert the rendering a user
  reads.** They previously read back the constructor arguments they had just
  passed in and then checked only that `str(...)` was *truthy* — a dataclass
  cannot fail to store its own fields, so those tests inflated apparent coverage
  of `build_data_classes.py` without being able to fail. They now pin the
  `__str__` output, including that the optional `Action:` and `Job ID:` lines
  are omitted when unset.
