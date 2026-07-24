- **Cassettes are no longer parsed with an unsafe YAML loader.** HTTP-replay
  cassettes were deserialized with PyYAML's `CLoader`, whose constructor chain
  reaches `UnsafeConstructor` before `SafeConstructor`, so a YAML tag such as
  `!!python/object/apply` executed during parsing — before any schema check
  could reject the document. Cassettes are tracked files in course repositories
  (`.gitignore` deliberately un-ignores `.clm/cassettes/`) and are parsed
  host-side by `clm build`, so a one-line edit arriving in a pull request could
  run arbitrary code as the user running the build. CLM now uses `CSafeLoader`
  (`SafeLoader` without libyaml). The v1 cassette format uses only scalars,
  maps, sequences and `!!binary`, all of which the safe loader supports, so no
  existing cassette becomes unreadable. **Upgrading is recommended for anyone
  who builds course repositories they do not fully control.**
