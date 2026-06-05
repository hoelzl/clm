<!-- HANDOVER-ARCHIVE — fully retired on 2026-06-05 -->

# Handover Archive: Full-deck translation — `clm slides translate` (deck bootstrap)

> ⚠️ **FULLY RETIRED HANDOVER — NOT ACTIVE**
>
> This document archives a handover whose work is fully complete. **There is no
> active handover document.** It must **not** be used with `/resume-feature`,
> `/implement-next-phase`, or similar commands that expect an active work plan.
>
> Feature shipped via **PR #234** (Issue #232); all 5 phases merged onto branch
> `claude/slides-translate-bootstrap`. If you need to resume related work, start a
> fresh handover.

---

# Handover: Full-deck translation — `clm slides translate` (deck bootstrap)

**Status:** ✅ **FEATURE COMPLETE — all 5 phases DONE.** Phase 1 = pure engine `translate_deck.py`. Phase 2 = orchestration `translate_bootstrap.py` (D2 dispatch + mint + watermark). Phase 3 = voiceover companion in lockstep. Phase 4 = the `clm slides translate` (+ `bootstrap` alias) CLI command + `TranslationCache`/`CachingSlideTranslator`. Phase 5 = info-topic docs (`commands.md` + `migration.md`) + the validate acceptance gate. All green; lint/format/mypy clean; fast suite green. Shipped via **PR #234**.
**Branch:** `claude/slides-translate-bootstrap` off `master` (`fdf7772` handover, `cf1c8e06` Phase 1, `29947b88` Phase 2, `c9199c8a` Phase 3, `28511cb2` Phase 4, `23ed7b87` Phase 5).
**Builds on (all merged to master):** the resolve-then-apply sync engine (#166/#190/#216), cold-start mint/adopt (#216), committed un-bootstrapped pairs (#225), partial-overlap mismatch (#226).
**Related design notes:** `docs/claude/design/single-language-authoring-sync.md`, `docs/claude/design/sync-plan-resolve-apply.md`.
**Sibling handover:** `docs/claude/sync-plan-resolve-apply-handover.md` (the engine this feature reuses).

> One-line orientation: this is **orchestration over existing primitives**, not new machinery. The translator, role-aware prompts, id parity, lossless cell round-trip, and a test fake all already exist. The new code is (a) a pure bootstrap engine that loops the existing per-cell translator over a whole source half, (b) a thin CLI command, and (c) a "twin already exists → just run sync" delegation that makes re-runs converge to incremental sync.

---

## 1. Feature Overview

When an author writes a slide deck in a **single language** (e.g. only `slides_x.de.py`), there is currently **no tool to generate the other-language half**. `clm slides sync` deliberately refuses: `_resolve_single_path` (`src/clm/cli/commands/slides_sync.py:148`) raises `UsageError("no EN twin found ... rather than invent a full translated half")`. Sync only fills **per-cell** gaps inside an **already-existing** pair.

This feature adds **`clm slides translate SOURCE`** (alias `bootstrap`): a one-shot, one-directional, full-deck translation that synthesizes the missing-language split half. Code is mostly **not** translated — controlled by the existing lang-tag mechanism (see Design Decision D3). After the twin exists, the command **delegates to `sync`**, so the author has a single lifecycle: `translate` once → `sync` forever after.

**Why it matters:** authoring a bilingual deck currently means hand-writing both halves or hand-copying+translating cell by cell. This automates the cold-start translation while keeping the result immediately valid for the split-pair tooling (`sync`, `unify`, validators).

**Issues/PRs:** tracking issue **#232** (https://github.com/hoelzl/clm/issues/232); shipped in **PR #234**.

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

The gate is the existing `role_of` (`src/clm/slides/sync_writeback.py:66`) / `_membership_role` (`src/clm/slides/sync_plan.py`). **The bootstrap engine MUST reuse this gate, not re-implement it** — translating a neutral/shared cell breaks the `unify` round-trip (`UnifyError`) and trips `validator._check_shared_cell_parity`. *(See the Phase 1 discovery: the implemented gate is actually `metadata.lang`, which subsumes this — `role_of` is `None` for an id-less localized code cell that must still translate.)*

### D4 — Output shape: split sibling `.en.py` *(user-confirmed)*
`translate slides_x.de.py` writes the sibling split half `slides_x.en.py`. Matches `sync`, the validator, and `assign_ids_in_split_pair` directly. If the author wants a bilingual file, they run `clm slides unify` afterward. (Rejected: emit a unified bilingual `.py` — needs an extra split before feeding split-pair tooling, second code path.)

### D5 — Voiceover companion translated in lockstep *(user-confirmed)*
If `voiceover_<name>.<src>.py` exists, translate its localized cells too (preserving `for_slide` / `vo_anchor`) and write `voiceover_<name>.<tgt>.py`, placed in the source companion's directory (sibling or `voiceover/` subdir). Skipping it would ship the new deck with source-language narration. Note `derive_split_twin` returns `None` for `voiceover_*`, so the companion is handled explicitly, not as a deck half.

### D6 — Smaller decisions (defaults)
- **Direction:** inferred from the source half's `.de`/`.en` tag (`.de.py` → produces `.en`), with an optional **`--to en|de`** override for safety when a source mixes/omits lang tags.
- **ID authority:** EN-authority `de_id == en_id` via `assign_ids_in_split_pair`, run over the freshly-written pair. If the source half is itself **id-less**, mint at bootstrap so the pair is **never born id-less** (an id-less/half-id'd pair would otherwise force a downstream cold-mint/adopt round needing the correspondence verifier + key).
- **Performance:** synchronous per-cell loop, backed by a `TranslationCache` (shared `clm-llm.sqlite` shape) so re-runs/tests are cheap. Async fan-out (`client.py` `_get_semaphore` pattern, default `max_concurrent=5`) is a later optimization only if real decks are too slow.
- **`--force`:** overwrite an existing **non-empty** twin; without it, an existing twin → delegate to sync (per D2), never silent overwrite.
- **Bilingual-stem source:** if `SOURCE` is a bilingual deck (no `.de`/`.en` tag) rather than a single half, reject with a hint to run `clm slides split` first (keep the contract explicit).

---

## 3. Phase Breakdown

### Phase 1 — Core bootstrap engine `src/clm/slides/translate_deck.py` (pure, offline) — [DONE]
**Accomplished:** `translate_deck_text(source_text, *, source_lang, target_lang, translator) -> TranslateDeckResult` turns one source half's text into the translated target half's text, deterministically and without network. Public API: `translate_deck_text`, `TranslateDeckError`, `TranslateDeckResult` (`.target_text`, `.cells`, `.translated_count`, `.copied_count`, `.header_translated`).

- Parses the source with `raw_cells.split_cells` (byte-faithful). Per cell:
  - **header macro** (`header_<src>`) → rewrite to `header_<tgt>` + translate only the title string;
  - **header import** (`from … import header_<src>`) → rewrite to `header_<tgt>`;
  - **localized** (`metadata.lang == source_lang`) → translate via the `SlideTranslator` protocol and `build_twin_cell`, re-appending the source cell's trailing-blank count to preserve spacing;
  - **everything else** (no-lang shared cells incl. code, non-header j2) → copy verbatim.
- **Key correction vs. the original plan:** the translate-vs-copy gate is **`metadata.lang`**, NOT `role_of`. A localized *id-less* code cell has `role_of() is None` yet carries `lang` and must be translated. `role_of`/`cell_type` only pick the prompt (`CODE_ROLE` → code prompt).
- Header grammar is **reused** from `split.py` (`_HEADER_DE_RE`/`_HEADER_EN_RE` + import REs), not re-implemented.
- **Safety guard:** before returning, the engine self-checks `split(unify(de, en)) == (de, en)` (ordering the two halves by language — `unify_texts` takes `(de, en)` positionally) and raises `TranslateDeckError` rather than emit a structurally-malformed pair. All-or-nothing — never half-writes; on a per-cell `TranslationError` it raises naming the slide.
- **Tests:** `tests/slides/test_translate_deck.py` (17, all green) — round-trip + content on every shape (slide / voiceover / localized-code / id-less-code / shared / no-header / reverse-direction), byte-exact on trailing-symmetric decks, and the error paths.

### Phase 2 — Idempotency + sync convergence + watermark — [DONE]
**Accomplished:** the D2 dispatch and the "next sync is a no-op" seal, in `src/clm/slides/translate_bootstrap.py`.

- `derive_bootstrap_paths(source_path, target_lang=None) -> BootstrapPaths`: resolves direction (`split_lang_tag`, with a `--to` override), derives the twin path **existence-agnostically** (`_twin_path` swaps the second-to-last dotted segment — `derive_split_twin` could not be reused because it returns `None` when the twin is *absent*, which is exactly the bootstrap case), and resolves all paths so the watermark key matches a later `clm slides sync`. Rejects a bilingual stem (no tag), a `voiceover_*` source, and a contradictory/unsupported `--to`. `twin_exists` folds in **non-emptiness** (an empty twin is treated as absent).
- `bootstrap_deck(...)`: the dispatch. **Twin present** (and not `--force`) → `_delegate_to_sync` = `build_sync_plan` + `apply_plan` mirroring `slides_sync_cmd`. **Twin absent / empty / `--force`** → `_bootstrap_new_twin`: `translate_deck_text` → `write_text(newline="\n")` → `assign_ids_in_split_pair(de, en, AssignOptions(accept_content_derived=True))` (EN-authority parity; mints onto **both** halves if the source was id-less; `force=False` preserves any author id) → `_record_watermark`.
- Pure orchestration over **injected** deps (translator/judge/recoverer/verifier/caches) — builds no LLM client, loads no `.env`, closes no cache (the CLI owns those). Offline-testable through the `SlideTranslator`/`SyncJudge` protocols.
- **Result:** `BootstrapResult` (`action` = `"bootstrapped"`/`"synced"`, `deck`/`assign` or `plan`/`apply_result`, `ids_assigned`, `watermark_recorded`).
- **Acceptance — met:** `translate` twice → second run is a `sync` no-op (`action=="synced"`, `proposals==[]`, no errors, deck not doubled), proven for **both** an id'd and an **id-less** source (the latter rewrites both halves on run 1, so it exercises the post-mint watermark). Round-trip + parity hold on the written files, including a **trailing-asymmetric** deck (ends on a slide pair). 19 tests added (28 total after Phase 3) in `tests/slides/test_translate_bootstrap.py`.

### Phase 3 — Voiceover companion in lockstep — [DONE]
**Accomplished:** D5, inside `translate_bootstrap.py`.

- `_translate_companion(paths, translator, force)`: on the **bootstrap** path only, `resolve_companion(source_path)` finds the source half's companion (sibling **or** `voiceover/` subdir). The target is `companion_name(twin_path)` placed in the **source companion's directory** (foldered stays foldered) — mirroring `split._plan_companion_split`. It is translated by the **same** `translate_deck_text` (a companion is just `lang`-tagged narrative cells, no header macro), so `build_twin_cell` preserves `for_slide` / `vo_anchor` / `slide_id` / `tags` verbatim (only `lang` + body change).
- **All-or-nothing:** both the deck and the companion are translated *before any write*, so a companion translation failure aborts the whole bootstrap with nothing on disk (test asserts this).
- **Idempotency:** an existing non-empty target companion is `"skipped"` (never doubled) unless `--force`. The **sync-delegation** path does not touch the companion (`result.companion is None`), so a re-run can't double it.
- **Result:** `BootstrapResult.companion: CompanionResult | None` (`action` = `"translated"`/`"skipped"`, `source`, `target`, `translation`).
- **Acceptance — met:** companion translated alongside the deck; `for_slide` + `vo_anchor` preserved; companion pair round-trips; subdir layout preserved; existing target skipped; `--force` regenerates; re-run via sync leaves it untouched; translation failure writes nothing. 9 companion tests (28 total in `tests/slides/test_translate_bootstrap.py`).
- **Deferred:** full companion *sync* (reconciling an already-present companion when the deck twin exists) is out of scope — the bootstrap creates it once; later deck syncs leave it alone. Also not special-cased: a companion whose `for_slide` references an **id-less** deck (a malformed input — `extract` requires deck ids; the bootstrap mint with `force=False` preserves existing ids, so a real companion's `for_slide` stays valid).

### Phase 4 — CLI command `clm slides translate` (+ `bootstrap` alias) — [DONE]
- **`src/clm/cli/commands/slides_translate.py`** — the Click command wrapping `bootstrap_deck`. Options: `--to en|de`, `--dry-run` (side-effect-free preview: counts translatable-vs-copied + target + companion, **no LLM/key**, exit 0), `--json`, `--force`, `--translation-model`, `--provider` (judge backend for the delegated-sync path only), `--llm-model`, `--cache-dir`, `--no-cache`, `--no-env-file`. It calls `derive_bootstrap_paths` first (pure) to decide bootstrap-vs-sync **before** building any client, builds the translator always and the judge (via reused `slides_sync._resolve_judge`) only on the sync path. Recoverer/verifier are intentionally **off** (a bootstrap-created pair is already id'd+watermarked; advanced id-migration lives on `clm slides sync`).
- **No key → exit 1, write nothing** — but only on the **bootstrap** path (a whole untranslated deck is useless). The delegated-sync path degrades like `clm slides sync` (adds defer). `_make_translator` is a module-level factory so tests patch it with a static translator; `has_openrouter_api_key` is imported into the module namespace and patched in tests.
- **`TranslationCache`** in `src/clm/infrastructure/llm/cache.py` (table `translations`, PK `(content_hash, prompt_version, source_lang, target_lang, role)`, get/put/invalidate/close; only successful results) + **`CachingSlideTranslator`** in `sync_translate.py` wrapping a `SlideTranslator`, `prompt_version` computed in `__post_init__` (a settable field, **not** a property — a read-only property fails the Protocol's settable `prompt_version: str` under mypy) with the model folded in so two models never share an entry.
- Registered in `src/clm/cli/main.py` (~line 167) under `slides_group` for both `translate` and `bootstrap` (same command object, two names — Click handles this; no optional-import guard needed, the deps are core).
- Exit codes: `0` wrote/clean, `1` delegated-sync deferred / no key, `2` hard error (bad source → `UsageError`; `TranslateDeckError` mid-bootstrap → caught, exit 2, nothing written).
- **Acceptance — met:** `CliRunner` tests (11) — absent-twin bootstrap (exit 0, twin == split's EN), no-key exit 1 + nothing written, `--dry-run` no-write + `--json` shape, `--to` reverse direction, present-twin → incremental-sync no-op (not doubled), `--force` overwrite, bilingual-stem/missing-source exit 2, and the `bootstrap` alias via the `slides` group. Plus 13 cache/caching-translator unit tests in `tests/infrastructure/llm/test_translation_cache.py`.

### Phase 5 — Docs & acceptance gate — [DONE]
- Added a `### clm slides translate` section to `src/clm/cli/info_topics/commands.md` (option table, exit codes, examples, the `bootstrap` alias, the lang-tag rule, companion lockstep, dispatch/idempotency), with `{version}` placeholders. Also updated the `### clm slides sync` cross-reference ("sync never invents a translated half") to point at `clm slides translate` for the cold start.
- Added a `## Bootstrap a second language: clm slides translate ({version} — additive)` entry to `migration.md` (the `translate` → `sync` → `unify` workflow; additive, no break).
- **MCP: intentionally skipped** — the MCP surface wraps read/analysis library functions (`suggest_sync`, `validate`, `normalize`); the writing+LLM `slides_sync` is **not** exposed there, so `slides_translate` is out of scope by the same rationale (not an omission).
- **Acceptance gate — met:** `test_generated_deck_passes_validate_fail_on_warning` bootstraps a deck through the **real CLI** and asserts `clm validate <dir> --fail-on warning` exits 0 (slide_id set/order parity, shared-cell byte parity, pairing adjacency, companion `for_slide` parity all hold on the generated pair).

---

## 4. Final Status (at retirement)

- **Completed:** design + **all 5 phases**. `cf1c8e06` Phase 1, `29947b88` Phase 2, `c9199c8a` Phase 3, `28511cb2` Phase 4, `23ed7b87` Phase 5. Lint/format/mypy clean; fast suite green (6664 passing). Issue **#232**; shipped via **PR #234**.
- **Tests (70 total):** 17 (`test_translate_deck`) + 28 (`test_translate_bootstrap`) + 13 (`test_translation_cache`) + 12 (`test_slides_translate`, incl. the acceptance gate).
- **The `--to` question** is resolved: `derive_bootstrap_paths` requires a `.de`/`.en` tag on the *source* (a tag-less single half is rejected with a "run split first" hint), and `--to` is an optional override.

---

## 5. Optional Future Work (not required)

- Async fan-out of the per-cell translate loop (`client.py` `_get_semaphore`, default `max_concurrent=5`) if real decks are slow.
- Full companion *sync* (reconciling an already-present companion on the sync-delegation path).
- A `--to` requirement when a source genuinely has no lang tags (currently inferred from the filename tag).

---

## 6. Key Files & Architecture

### New files
- **`src/clm/slides/translate_deck.py` (Phase 1).** The pure, offline bootstrap engine: `translate_deck_text(source_text, *, source_lang, target_lang, translator) -> TranslateDeckResult`. Parses a source half, classifies cells by `metadata.lang`, translates localized ones (code via `CODE_ROLE`), copies neutral ones verbatim, rewrites the header macro/import (reusing `split.py` regexes), self-checks the split/unify round-trip, returns target text. Protocol-driven (`SlideTranslator`) so it's offline-testable.
- **`src/clm/slides/translate_bootstrap.py` (Phases 2 + 3).** File/orchestration layer: `derive_bootstrap_paths` (direction + existence-agnostic twin path + rejections), `bootstrap_deck` (the D2 dispatch), `_bootstrap_new_twin` (translate deck + companion → write both → `assign_ids_in_split_pair` → `_record_watermark`), `_translate_companion` (`resolve_companion` + `companion_name`, all-or-nothing, skip/force), `_delegate_to_sync` (mirror of `slides_sync_cmd`). Public surface: `bootstrap_deck`, `derive_bootstrap_paths`, `BootstrapResult`, `CompanionResult`, `BootstrapPaths`, `TranslateBootstrapError`.
- **`src/clm/cli/commands/slides_translate.py` (Phase 4).** The `clm slides translate` (+ `bootstrap`) Click command; `_make_translator` factory (patchable), `derive_bootstrap_paths`-first dispatch, `--dry-run` preview, `--json`, exit-code matrix.
- **`TranslationCache` (`src/clm/infrastructure/llm/cache.py`) + `CachingSlideTranslator` (`src/clm/slides/sync_translate.py`) (Phase 4).**
- Tests: `tests/slides/test_translate_deck.py` (17), `tests/slides/test_translate_bootstrap.py` (28), `tests/infrastructure/llm/test_translation_cache.py` (13), `tests/cli/test_slides_translate.py` (12).

### Modified files
- `src/clm/cli/main.py` — registers `translate` + `bootstrap` under `slides_group` (~line 167).
- `src/clm/cli/info_topics/commands.md` — `### clm slides translate` section + sync cross-reference.
- `src/clm/cli/info_topics/migration.md` — `## Bootstrap a second language` entry.

### Reuse surface (verified at these paths/lines at time of writing)
| Symbol | Location | Role |
|---|---|---|
| `SlideTranslator` (Protocol) / `OpenRouterSlideTranslator` / `StaticSlideTranslator` | `src/clm/slides/sync_translate.py:45 / :96 / :69` | per-cell translator; `translate(*, source_body, source_lang, target_lang, role) -> str`. `StaticSlideTranslator` = deterministic test fake. |
| `_system_prompt_for` / `_CODE_SYSTEM_PROMPT` / `DEFAULT_TRANSLATION_MODEL` | `sync_translate.py:188 / :174 / :37` | role→prompt selection; code prompt keeps identifiers byte-identical; model `anthropic/claude-sonnet-4-6`. |
| `role_of` | `src/clm/slides/sync_writeback.py:66` | the translate-vs-copy gate (keyed on `lang` presence + cell_type). |
| `swap_lang` / `build_twin_cell` | `sync_writeback.py:228 / :241` | lossless twin-cell construction. |
| `split_cells` / `reconstruct` | `src/clm/slides/raw_cells.py:53 / :95` | byte-faithful parse→write foundation. |
| `assign_ids_in_split_pair` | `src/clm/slides/assign_ids.py:823` | EN-authority shared-id minting (`de_id==en_id`) with round-trip guard. |
| header-macro regexes + `split_text` / `unify_texts` | `src/clm/slides/split.py` | `header_de`↔`header_en` swap; the round-trip invariant to assert. |
| `derive_split_twin` / `split_lang_tag` / `order_split_pair` | `src/clm/slides/pairing.py:210 / :158 / :189` | target-path derivation + twin-missing detection. |
| `build_sync_plan` / `apply_plan` | `src/clm/slides/sync_plan.py:1664` / `sync_apply.py:208` | delegated to when the twin already exists (D2). |
| `_record_watermark` / `SyncWatermarkCache` | `sync_apply.py:2172` / `src/clm/infrastructure/llm/cache.py:652` | idempotency seal. |
| `resolve_companion` / `companion_name` | `src/clm/slides/voiceover_tools.py:233 / :181` | companion resolution + naming (D5). |
| `has_openrouter_api_key` / `build_openrouter_client` | `src/clm/infrastructure/llm/openrouter_client.py:71 / :81` | provider/key wiring. |
| `load_env_files` | `src/clm/cli/env_loading.py:45` | `.env` walk-up (respect `--no-env-file`). |

### How it connects
`slides_translate.py` (CLI) → resolves source + twin via `derive_bootstrap_paths` → **twin present**: `build_sync_plan`+`apply_plan` (existing sync) — **twin absent**: `translate_deck.py` (new engine, drives `SlideTranslator`) → `assign_ids_in_split_pair` → `_record_watermark`. Companion handled in lockstep. Validation gate is `clm validate slides`.

---

## 7. Testing Approach

- **Unit (primary, offline):** drive everything through the `SlideTranslator` **Protocol** with `StaticSlideTranslator` — **no network, no vcrpy cassette** (this is a host-side path, unlike in-kernel build LLM traffic). The whole sync stack is tested this way.
- **Engine invariants (17 tests):** the primary assertion is `split(unify(de, en)) == (de, en)` (`_assert_valid_pair`) plus content checks. Byte-exact `== other_half` is asserted only on *trailing-symmetric* decks (header-only or shared-cell-terminated). The strongest cases use a `_mirror_translator` built from the canonical split halves so a correct engine regenerates the other side's content exactly.
- **Bootstrap (28 tests):** run `translate` twice → second run = `sync` no-op (assert no doubling, watermark recorded). Companion translated, `for_slide`/`vo_anchor` preserved.
- **CLI (12 tests):** `CliRunner` for absent/present twin, `--dry-run` writes nothing, `--json` shape, no-key degradation (exit 1, nothing written), `--force`, exit-code matrix, and the acceptance gate (`clm validate <dir> --fail-on warning` passes). Note the Click 8.1-vs-8.2 `CliRunner` compat pattern (memory `feedback-click-82-clirunner-compat`).
- **Run:** full fast suite `pytest` before push (pre-push hook, ~72s). The commit hook can flake on unrelated recordings polling under xdist contention — if so, `PYTEST_XDIST_AUTO_NUM_WORKERS=4 git commit`.

---

## 8. Session Notes / Discoveries

- The user is the project author and explicitly chose the **separate-command** surface, **split-sibling** output, and **lockstep voiceover** when asked.
- The "code is translated iff it has a lang tag" requirement was the user's own framing — and it happens to be exactly the existing `role_of` / no-lang-is-shared model. No parallel marker invented.

### Phase 1 discoveries (load-bearing)
- **The translate-vs-copy gate is `metadata.lang`, NOT `role_of`.** The original plan said "gate on `role_of(meta) is None`", but `role_of` returns `None` for a localized id-less code cell (`# %% lang="de"` with no `slide_id`) — which *must* be translated. So: `lang == source_lang` → translate; `lang is None` → copy verbatim. `role_of`/`cell_type` only select the prompt (`CODE_ROLE` → code prompt). Encoded in `_translation_role` and the main loop.
- **`split` produces trailing-blank-asymmetric halves.** The cell that ends the bilingual source carries an extra EOF blank line that, after `split`, lands on **only one** half. So a generated half (the engine mirrors the *source* half's per-cell trailing blanks) does **not** in general byte-match the other half of an arbitrary bilingual deck — and that is correct: when bootstrapping there is no pre-existing other half to match. The honest invariant is the round-trip, not byte-equality. Byte-exact tests therefore use decks that end on a shared cell (symmetric) or are header-only.
- **The round-trip guard verifies split-*validity*, not translation *fidelity*.** A translator that injects a *valid* `lang="en"` cell boundary produces an extra-but-valid cell that still round-trips (guard won't flag it); only a structurally-breaking injection (e.g. a no-lang cell appearing in one half) trips it. Fidelity is the LLM's job / a later verify pass, not the engine's.
- **The engine reuses `split.py`'s private header regexes** (`_HEADER_DE_RE` etc.) via `from clm.slides import split`. Deliberate (the "don't duplicate the header grammar" rule).
- **`CachingSlideTranslator.prompt_version` must be a settable field, not a property** — a read-only `@property` fails the `SlideTranslator` Protocol's settable `prompt_version: str` under mypy. Computed in `__post_init__`, model folded in.
