# Handover: Full-deck translation — `clm slides translate` (deck bootstrap)

**Status:** design accepted (user-confirmed forks below); **no code written yet** — Phase 1 is next.
**Recommended branch:** `claude/slides-translate-bootstrap` off `master` (this feature builds on the now-merged sync core; do **not** branch off `claude/issue-226-partial-overlap-mismatch`).
**Builds on (all merged to master):** the resolve-then-apply sync engine (#166/#190/#216), cold-start mint/adopt (#216), committed un-bootstrapped pairs (#225), partial-overlap mismatch (#226).
**Related design notes:** `docs/claude/design/single-language-authoring-sync.md`, `docs/claude/design/sync-plan-resolve-apply.md`.
**Sibling handover:** `docs/claude/sync-plan-resolve-apply-handover.md` (the engine this feature reuses).

> One-line orientation: this is **orchestration over existing primitives**, not new machinery. The translator, role-aware prompts, id parity, lossless cell round-trip, and a test fake all already exist. The new code is (a) a pure bootstrap engine that loops the existing per-cell translator over a whole source half, (b) a thin CLI command, and (c) a "twin already exists → just run sync" delegation that makes re-runs converge to incremental sync.

---

## 1. Feature Overview

When an author writes a slide deck in a **single language** (e.g. only `slides_x.de.py`), there is currently **no tool to generate the other-language half**. `clm slides sync` deliberately refuses: `_resolve_single_path` (`src/clm/cli/commands/slides_sync.py:148`) raises `UsageError("no EN twin found ... rather than invent a full translated half")`. Sync only fills **per-cell** gaps inside an **already-existing** pair.

This feature adds **`clm slides translate SOURCE`** (alias `bootstrap`): a one-shot, one-directional, full-deck translation that synthesizes the missing-language split half. Code is mostly **not** translated — controlled by the existing lang-tag mechanism (see Design Decision D3). After the twin exists, the command **delegates to `sync`**, so the author has a single lifecycle: `translate` once → `sync` forever after.

**Why it matters:** authoring a bilingual deck currently means hand-writing both halves or hand-copying+translating cell by cell. This automates the cold-start translation while keeping the result immediately valid for the split-pair tooling (`sync`, `unify`, validators).

**Issues/PRs:** tracking issue **#232** (https://github.com/hoelzl/clm/issues/232). (Loosely adjacent to the #158 1.8 gate, but independent of it.)

---

## 2. Design Decisions

### D1 — Separate command, NOT a mode of `sync` *(user-confirmed)*
`clm slides translate` is a new sibling command that **reuses sync's engine**, rather than a `--bootstrap` flag on `sync`.

- **Why:** `sync` is a *reconciler of an existing pair* (per-cell drift, conflict isolation, watermarks, refuse-on-ambiguity); bootstrap is the opposite shape (one source, no twin, no baseline, all adds one direction, no conflicts). Folding in would mean **relaxing the exact `_resolve_single_path` guard** the recent #216/#225/#226 hardening erected, on the most safety-critical module. CLM convention is also one-verb-per-command (`split`/`unify`, `extract`/`inline` are siblings, never `--mode` flags).
- **Footgun avoided:** if `sync` silently translated a whole deck whenever a twin was missing, a mistyped path would kick off an expensive surprise LLM run. A named command makes the intent explicit.
- **Rejected alternative:** `sync --bootstrap`. Honors "always the same tool" literally but threads a not-yet-existent-file case through baseline resolution and the `_refuse_*_both_directions` guards — high blast radius for the same outcome.

### D2 — Idempotency by delegation *(core safety property)*
The command's central dispatch:
- **Twin absent** → run the bootstrap engine (Phase 1).
- **Twin present** → do **not** bootstrap; delegate straight to `build_sync_plan` + `apply_plan` exactly as `slides_sync_cmd` does.

Re-running therefore **converges to plain `sync` by construction** — never re-translates the whole deck, never doubles. After a bootstrap write, **record the watermark** so the very next `sync` is a clean no-op.

### D3 — Lang tags already control code-vs-prose translation *(user's instinct, confirmed by the code)*
No new mechanism needed:
- A cell with **no `lang` attribute** is *neutral/shared* → copied **byte-for-byte** into both halves, **never translated** (this is how code cells behave idiomatically).
- A cell **with a `lang` tag** is *localized* → translated. `role == "code"` selects `_CODE_SYSTEM_PROMPT` (localizes **only** human-facing string literals + comments, keeps identifiers/keywords byte-identical); markdown/narrative roles select `_SYSTEM_PROMPT`.

The gate is the existing `role_of` (`src/clm/slides/sync_writeback.py:66`) / `_membership_role` (`src/clm/slides/sync_plan.py`). **The bootstrap engine MUST reuse this gate, not re-implement it** — translating a neutral/shared cell breaks the `unify` round-trip (`UnifyError`) and trips `validator._check_shared_cell_parity`.

### D4 — Output shape: split sibling `.en.py` *(user-confirmed)*
`translate slides_x.de.py` writes the sibling split half `slides_x.en.py`. Matches `sync`, the validator, and `assign_ids_in_split_pair` directly. If the author wants a bilingual file, they run `clm slides unify` afterward. (Rejected: emit a unified bilingual `.py` — needs an extra split before feeding split-pair tooling, second code path.)

### D5 — Voiceover companion translated in lockstep *(user-confirmed)*
If `voiceover_<name>.<src>.py` exists, translate its localized cells too (preserving `for_slide` / `vo_anchor`) and write `voiceover_<name>.<tgt>.py`, placed via `effective_write_layout`. Skipping it would ship the new deck with source-language narration. Note `derive_split_twin` returns `None` for `voiceover_*`, so the companion is handled explicitly, not as a deck half.

### D6 — Smaller decisions (defaults; not re-litigated unless the user objects)
- **Direction:** inferred from the source half's `.de`/`.en` tag (`.de.py` → produces `.en`), with an optional **`--to en|de`** override for safety when a source mixes/omits lang tags.
- **ID authority:** EN-authority `de_id == en_id` via `assign_ids_in_split_pair`, run over the freshly-written pair. If the source half is itself **id-less**, mint at bootstrap so the pair is **never born id-less** (an id-less/half-id'd pair would otherwise force a downstream cold-mint/adopt round needing the correspondence verifier + key).
- **Performance:** synchronous per-cell loop to start (consistent with the apply engine), backed by a new `TranslationCache` (shared `clm-llm.sqlite` shape) so re-runs/tests are cheap. Async fan-out (`client.py` `_get_semaphore` pattern, default `max_concurrent=5`) is a later optimization only if real decks are too slow.
- **`--force`:** overwrite an existing **non-empty** twin; without it, an existing twin → delegate to sync (per D2), never silent overwrite.
- **Bilingual-stem source:** if `SOURCE` is a bilingual deck (no `.de`/`.en` tag) rather than a single half, reject with a hint to run `clm slides split` first (keep the contract explicit).

---

## 3. Phase Breakdown

### Phase 1 — Core bootstrap engine `src/clm/slides/translate_deck.py` (pure, offline) — [TODO] ← NEXT
**Accomplishes:** turn one source half's text into the translated target half's text, deterministically and without network.

- Parse the source with `raw_cells.split_cells` (`src/clm/slides/raw_cells.py:53`) — **byte-faithful**. Never use `slide_parser.parse_cells` (lossy; read-only analysis only).
- For each `RawCell`, classify with `role_of` (`sync_writeback.py:66`) / `_membership_role` (`sync_plan.py`):
  - neutral / no-`lang` (incl. idiomatic code) → **copy verbatim** into the target half.
  - localized → translate via the `SlideTranslator` protocol (`sync_translate.py:45`), `role` selecting code-vs-markdown prompt.
- Title slide is a **j2 header macro**, not a normal cell: swap `header_de("…")` ↔ `header_en("…")` **and** the `import header_de`/`import header_en` line **structurally** using `split.py`'s header regexes — translate only the title string argument. Running the macro through the cell translator corrupts it.
- Build twins with `build_twin_cell` (`sync_writeback.py:241`) / `swap_lang` (`sync_writeback.py:228`) — preserves `slide_id` + tags + cell-type, swaps `lang`. Order cells so the translated twin sits adjacent to its source under the same id (so `unify` can interleave).
- Stamp the source half's `slide_id`s onto the twins (carried through `swap_lang`).
- New `TranslationCache` in `src/clm/infrastructure/llm/cache.py` (one table in the shared `clm-llm.sqlite`; key = content-hash + **model-folded** `prompt_version`; cache only successful results — safe-abort caches nothing).
- **Acceptance:** given a source half + a `StaticSlideTranslator`, produce the target text such that `split(unify(de, en)) == (de, en)` and `unify(de, en)` round-trips. Neutral cells byte-identical across halves. Unit tests only, no network.

### Phase 2 — Idempotency + sync convergence + watermark — [TODO]
**Accomplishes:** the D2 dispatch and the "next sync is a no-op" seal.

- Resolve source + derive twin with `derive_split_twin` (`pairing.py:210`) / `split_lang_tag` (`pairing.py:158`).
- **Twin present** → delegate to `build_sync_plan` (`sync_plan.py:1664`) + `apply_plan` (`sync_apply.py:208`) (mirror `slides_sync_cmd`'s wiring). **Twin absent** → Phase 1 bootstrap, then:
  - run `assign_ids_in_split_pair` (`assign_ids.py:823`, `AssignOptions(accept_content_derived=True)`) for EN-authority parity (re-slugs from EN headings; writes the **source** half back too if ids changed — keep parity).
  - record the watermark via `_record_watermark` (`sync_apply.py:2172`) + `SyncWatermarkCache` (`cache.py:652`).
- **Acceptance:** `translate` twice → the second run is a `sync` no-op; deck is not doubled; a subsequent `clm slides sync` reports clean. Round-trip + parity assertions hold on the written files.

### Phase 3 — Voiceover companion in lockstep — [TODO]
**Accomplishes:** D5.

- Detect `voiceover_<name>.<src>.py`; translate localized cells preserving `for_slide` / `vo_anchor`; write `voiceover_<name>.<tgt>.py` via `effective_write_layout` (`src/clm/slides/sidecar_layout.py`).
- **Acceptance:** companion `for_slide` parity holds against the translated deck (`validator._check_split_companion_for_slide_parity`); narration cells are translated, anchors preserved.

### Phase 4 — CLI command `clm slides translate` (+ `bootstrap` alias) — [TODO]
**Accomplishes:** user-facing surface.

- New module `src/clm/cli/commands/slides_translate.py`; clone the option skeleton from `slides_sync.py` and the simple-mutation skeleton from `split.py`:
  - `--to en|de`, `--dry-run` / `--explain` (side-effect-free: parse + classify + count translatable vs copied + show target path + id plan; skip LLM/.env where possible), `--json` (`_to_dict`: `cells_translated`, `cells_copied`, `target_path`, `ids_minted`, `deferred`), `--force`, `--provider` (env `CLM_SYNC_PROVIDER`), `--translation-model`, `--cache-dir` (env `CLM_CACHE_DIR`), `--no-cache`, `--no-env-file`.
- Reuse `has_openrouter_api_key` (`openrouter_client.py:71`), `build_openrouter_client`, `load_env_files` (`env_loading.py:45`), `resolve_cache_dir`. No key → **warn + degrade** (defer localized cells or exit 1), never crash. On `TranslationError`, defer-and-report (the never-drop-content discipline at `sync_apply.py` `_translate`).
- Register in `src/clm/cli/main.py` (~line 168): `slides_group.add_command(translate_cmd, name="translate")` and a second registration for `bootstrap`. Use the try/except optional-import pattern (main.py ~112-130) if LLM deps may be absent.
- Exit codes: `0` wrote/clean, `1` some cells deferred (no key) / needs review, `2` hard error (twin exists without `--force`, engine down, bilingual-stem source).
- **Acceptance:** `CliRunner` tests for absent-twin bootstrap, present-twin delegation, `--dry-run` no-write, `--json` shape, no-key degradation, `--force` overwrite, exit codes.

### Phase 5 — Docs & acceptance gate — [TODO]
**Accomplishes:** the CLAUDE.md Info Topics Maintenance Rule + end-to-end validation.

- Add a `### clm slides translate` section to `src/clm/cli/info_topics/commands.md`, modeled on the existing `### clm slides sync` block (option table + exit codes + examples). Use `{version}` placeholders — never hardcode versions.
- Add a `migration.md` entry: the "bootstrap a second language" workflow (`translate` → then `sync`).
- If exposed via MCP, add a `slides_translate` tool + a migration parity entry.
- **Acceptance gate:** a generated deck passes `clm validate slides <dir> --fail-on warning` (slide_id set+order parity, shared-cell byte parity, pairing adjacency, companion `for_slide` parity).

---

## 4. Current Status

- **Completed:** design only. The three user-facing forks are settled (D1, D4, D5). The reuse surface is verified against the live tree (all symbols in §6 exist at the cited paths/lines as of this writing).
- **In progress:** none — implementation not started.
- **Blockers / open questions:** none blocking. D6 defaults stand unless the user objects. One thing to confirm with the user at Phase 4: whether `--to` should be required when the source half has **no** lang tags at all (currently planned as: infer from filename, `--to` optional override).
- **Tests:** none yet. Strategy in §7.

---

## 5. Next Steps

**Start Phase 1.** Prerequisites/setup:
1. From this worktree, create the branch: `git fetch origin && git switch -C claude/slides-translate-bootstrap origin/master`.
2. Ensure the worktree venv is synced (`uv sync --extra all`) — a repo-root `.venv` silently resolves `clm` to the main repo (see memory `feedback-worktree-venv-sync`).
3. Read `src/clm/slides/sync_translate.py` (the translator + both prompts), `src/clm/slides/sync_writeback.py` (`role_of`, `swap_lang`, `build_twin_cell`, `FileState`), `src/clm/slides/split.py` (header-macro regexes + the `unify`/`split` round-trip invariant), and `src/clm/slides/sync_apply.py` `_add_one_direction` / `_translate` (the existing per-cell translate+mint+insert pattern to mirror).

**Gotchas to watch (these silently corrupt the pair):**
- Translating a neutral/shared cell → `UnifyError` + `_check_shared_cell_parity` failure. Gate strictly on `role_of(meta) is None` / `meta.lang is None`.
- Running the header macro through the cell translator, or forgetting the `import header_de`→`import header_en` line swap → broken title slide + split↔bilingual round-trip. Reuse `split.py`'s regexes.
- Emitting the twin id-less → forces a downstream cold-mint/adopt round (needs verifier + key). Mint ids at bootstrap.
- Missing watermark seal → the next `sync` re-diffs and may re-propose. The `_record_watermark` call is load-bearing for D2.
- Program extension may be `.cpp`/`.java` etc.; the `.de`/`.en` tag sits immediately before the final extension. Use `split_lang_tag` (prefix-agnostic) for naming — don't assume `.py`.
- Line endings: write with `newline="\n"`; never line-string surgery — use `raw_cells` / `FileState`.

---

## 6. Key Files & Architecture

### New files
- `src/clm/slides/translate_deck.py` — the pure bootstrap engine (Phase 1). Parses a source half, classifies cells, translates localized ones, copies neutral ones verbatim, swaps the header macro, builds twins, returns target text. Protocol-driven (`SlideTranslator`) so it's offline-testable.
- `src/clm/cli/commands/slides_translate.py` — the `clm slides translate` Click command (Phase 4).
- `TranslationCache` class added to `src/clm/infrastructure/llm/cache.py` (Phase 1).
- Tests: `tests/slides/test_translate_deck.py` (engine), `tests/cli/test_slides_translate.py` (CLI), companion/idempotency tests.

### Modified files
- `src/clm/cli/main.py` — register `translate` + `bootstrap` under `slides_group` (~line 168).
- `src/clm/cli/info_topics/commands.md` — `### clm slides translate` section (Phase 5).
- `src/clm/cli/info_topics/migration.md` — bootstrap-a-second-language workflow entry (Phase 5).

### Reuse surface (verified at these paths/lines)
| Symbol | Location | Role |
|---|---|---|
| `SlideTranslator` (Protocol) / `OpenRouterSlideTranslator` / `StaticSlideTranslator` | `src/clm/slides/sync_translate.py:45 / :96 / :69` | per-cell translator; `translate(*, source_body, source_lang, target_lang, role) -> str`. `StaticSlideTranslator` = deterministic test fake. |
| `_system_prompt_for` / `_CODE_SYSTEM_PROMPT` / `DEFAULT_TRANSLATION_MODEL` | `sync_translate.py:188 / :174 / :37` | role→prompt selection; code prompt keeps identifiers byte-identical; model `anthropic/claude-sonnet-4-6`. |
| `role_of` | `src/clm/slides/sync_writeback.py:66` | the translate-vs-copy gate (keyed on `lang` presence + cell_type). |
| `swap_lang` / `build_twin_cell` | `sync_writeback.py:228 / :241` | lossless twin-cell construction. |
| `FileState` | `sync_writeback.py` | batched, header/padding-preserving deck writer. |
| `split_cells` / `reconstruct` | `src/clm/slides/raw_cells.py:53 / :95` | byte-faithful parse→write foundation. |
| `assign_ids_in_split_pair` | `src/clm/slides/assign_ids.py:823` | EN-authority shared-id minting (`de_id==en_id`) with round-trip guard. |
| header-macro regexes + `split_text` / `unify_texts` | `src/clm/slides/split.py` | `header_de`↔`header_en` swap; the round-trip invariant to assert. |
| `derive_split_twin` / `split_lang_tag` / `order_split_pair` | `src/clm/slides/pairing.py:210 / :158 / :189` | target-path derivation + twin-missing detection. |
| `build_sync_plan` / `apply_plan` | `src/clm/slides/sync_plan.py:1664` / `sync_apply.py:208` | delegated to when the twin already exists (D2). |
| `_record_watermark` / `SyncWatermarkCache` | `sync_apply.py:2172` / `src/clm/infrastructure/llm/cache.py:652` | idempotency seal. |
| `has_openrouter_api_key` / `build_openrouter_client` | `src/clm/infrastructure/llm/openrouter_client.py:71` | provider/key wiring. |
| `load_env_files` | `src/clm/cli/env_loading.py:45` | `.env` walk-up (respect `--no-env-file`). |
| `call_with_retries` | `src/clm/infrastructure/llm/retry.py` | bounded backoff; re-raises on exhaustion (never silently drops a cell). |
| `effective_write_layout` | `src/clm/slides/sidecar_layout.py` | voiceover companion placement (D5). |
| `_resolve_single_path` (the guard we sit beside) | `src/clm/cli/commands/slides_sync.py:148` | the current refusal to invent a missing twin — reference, not modified. |

### How it connects
`slides_translate.py` (CLI) → resolves source + twin via `pairing` → **twin present**: `build_sync_plan`+`apply_plan` (existing sync) — **twin absent**: `translate_deck.py` (new engine, drives `SlideTranslator`) → `assign_ids_in_split_pair` → `_record_watermark`. Companion handled in lockstep. Validation gate is `clm validate slides`.

---

## 7. Testing Approach

- **Unit (primary, offline):** drive everything through the `SlideTranslator` **Protocol** with `StaticSlideTranslator` — **no network, no vcrpy cassette** (this is a host-side path, unlike in-kernel build LLM traffic). The whole sync stack is tested this way; follow it.
- **Phase 1 invariants to assert:** neutral cells byte-identical across halves; `split(unify(de,en)) == (de,en)`; localized markdown translated, code identifiers untouched (only string/comment payload changes under the static fake); header macro swapped (`header_de`→`header_en` + import line) with title arg translated; ids carried onto twins.
- **Phase 2:** run `translate` twice → second run = `sync` no-op (assert no doubling, watermark recorded, subsequent `sync` clean). Present-twin delegation path exercised.
- **Phase 3:** companion translated, `for_slide`/`vo_anchor` preserved, parity validator passes.
- **Phase 4:** `CliRunner` for absent/present twin, `--dry-run` writes nothing, `--json` envelope shape, no-key degradation (exit 1 + warning, no crash), `--force` overwrite, exit-code matrix. Note the Click 8.1-vs-8.2 `CliRunner` compat pattern (memory `feedback-click-82-clirunner-compat`).
- **Acceptance:** generated deck passes `clm validate slides <dir> --fail-on warning`.
- **Run:** `pytest tests/slides/test_translate_deck.py tests/cli/test_slides_translate.py` during dev; full fast suite `pytest` before push (pre-push hook, ~72s). The commit hook can flake on unrelated recordings polling under xdist contention — if so, `PYTEST_XDIST_AUTO_NUM_WORKERS=4 git commit` (memory `project-topic-sidecar-subdirs`).

---

## 8. Session Notes

- The user is the project author and explicitly chose the **separate-command** surface, **split-sibling** output, and **lockstep voiceover** when asked. Don't re-open these without cause.
- The "code is translated iff it has a lang tag" requirement was the user's own framing — and it happens to be exactly the existing `role_of` / no-lang-is-shared model. Lean on that; don't invent a parallel marker.
- Grounding for this design came from a multi-agent read of the sync engine, slide format, pairing/cold-start machinery, LLM infra, and CLI surface. The reuse table in §6 was re-verified against the live tree (symbol names + line numbers) before writing this doc.
- CLAUDE.md hard rules in play: type hints on public APIs; `attrs @define` internal / Pydantic at boundaries; `logging.getLogger(__name__)` never `print()`; Python over bash for any tooling; **update info topics** when CLI behavior changes (Phase 5 is not optional). Commits that fail a hook didn't happen — fix, re-stage, new commit (never `--amend` a rejected commit).
