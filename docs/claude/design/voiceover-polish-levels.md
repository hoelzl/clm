# Voiceover Polish Levels Design

## Overview

CLM's speaker-note cleanup can operate at different levels of editorial
aggressiveness.  The original design exposed a binary choice (`--mode
polished | verbatim`) on the voiceover commands.  Phase 1 replaces that
binary with a named `PolishLevel` enum and a matching `--polish-level`
CLI option, while keeping `--mode` alive as a deprecated shim.

---

## Phase 1 — PolishLevel enum and named levels

**Scope:** introduce the enum, the per-level prompt files, wire them up
through `polish_text()` and both CLI commands (`clm polish` and
`clm voiceover sync`).

### Files added/changed

| Path | Change |
|------|--------|
| `src/clm/notebooks/polish_levels/__init__.py` | `PolishLevel` StrEnum + `load_prompt()` |
| `src/clm/notebooks/polish_levels/standard.md` | System prompt extracted from old `SYSTEM_PROMPT` |
| `src/clm/notebooks/polish_levels/light.md` | New lighter-touch system prompt |
| `src/clm/notebooks/polish_levels/heavy.md` | New heavier-touch system prompt |
| `src/clm/notebooks/polish_levels/rewrite.md` | New full-rewrite system prompt |
| `src/clm/notebooks/polish.py` | `polish_text()` gains `polish_level` kwarg; `verbatim` is a no-LLM passthrough |
| `src/clm/cli/commands/polish.py` | `--polish-level` option (default: `standard`) |
| `src/clm/cli/commands/voiceover.py` | `--polish-level` option on `sync` and `sync-at-rev`; `--mode` kept as deprecated shim |
| `src/clm/cli/info_topics/commands.md` | Documented `--polish-level`, noted `--mode` deprecation |

### PolishLevel enum

```python
class PolishLevel(StrEnum):
    verbatim = "verbatim"  # no LLM call — returns input unchanged
    light    = "light"
    standard = "standard"  # default; matches the old "polished" mode
    heavy    = "heavy"
    rewrite  = "rewrite"
```

`PolishLevel.verbatim` has no prompt file.  `load_prompt(PolishLevel.verbatim)`
raises `ValueError`.

### Deprecation mapping for --mode

| Old `--mode` value | Equivalent `--polish-level` |
|---|---|
| `polished` | `standard` |
| `verbatim` | `verbatim` |

The `--mode` option is retained on `clm voiceover sync` and
`clm voiceover sync-at-rev` with a `DeprecationWarning` and a help-text
note.  Passing both `--mode` and `--polish-level` together is rejected
with a `UsageError`.  `--mode` will be removed in a future minor release.

### Verbatim passthrough

When `polish_level == PolishLevel.verbatim` the `polish_text()` function
returns the input string immediately without importing or calling the LLM
client.  No API key or network access is required.

---

## Future phases (not yet implemented)

- **Phase 2**: Persist `polish_level` in the voiceover trace log and expose
  it in `clm voiceover trace show` output.
- **Phase 3**: Remove `--mode` entirely (requires a major-version bump or a
  dedicated deprecation-removal release).
