- **The `vcrpy` dependency is gone** (issue #355 stage 2). The cassette
  format (vcrpy v1 YAML — unchanged on disk, byte-for-byte) is now
  implemented by CLM itself in `http_replay_mitm/vcr_format.py` (vendored
  from vcrpy 8.1.1, MIT) and needs only PyYAML. **No cassette re-recording is
  required**: the new serializer was validated byte-identical to vcrpy via a
  differential check (`scripts/differential_check_vcr_format.py`) and a
  round-trip of all 2,072 committed course cassettes, and the bytes are
  pinned permanently by a golden-fixture test. The `[replay]` extra now
  pulls `mitmproxy`, `pyyaml`, and `filelock`; an isolated `mitmdump` tool
  environment needs `uv tool install mitmproxy --with pyyaml` (environments
  installed with the old `--with vcrpy` keep working, since vcrpy depended
  on PyYAML).
