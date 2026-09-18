- **Commented-out package installs no longer run during builds (#904).**
  jupytext's percent reader reactivates commented magics and shell escapes
  (`# %%time` → `%%time`), which is how magics are meant to be written in a
  `.py` slide file — but it also turned the sanctioned `# !pip install …`
  install *hint* into an active cell, so `clm build` executed pip during
  notebook execution: against whatever `pip` was first on PATH (course
  packages landed in unrelated venvs) and through the HTTP-replay proxy as
  untagged, non-hermetic traffic. Students also received an active install
  cell the trainer meant to uncomment live. The notebook processor now keeps a
  commented **package-install command** exactly as written (`!pip`, `!pip3`,
  `!python -m pip`, `!uv pip` / `!uv add`, `!conda` / `!mamba`, `!poetry add`,
  `%pip` / `%conda`, plus `sudo`/`apt`/`brew`/`npm`-style system installs)
  while every other magic keeps jupytext's semantics — `# %%time`,
  `# %load_ext`, `# !python script.py` still run, `# # !echo` (double comment)
  still builds as a comment, and an install written *uncommented* stays
  active. Implemented by comparing against a `comment_magics=False` read, so
  jupytext's own quote/string parsing stays authoritative; the second read
  only happens when the source contains a candidate line. Documented in
  `clm info slide-format` ("Magics, shell escapes, and install hints").
