# Handover: Full-deck translation — `clm slides translate` (deck bootstrap)

**Status:** **Phase 1 + Phase 2 DONE.** Phase 1 = pure engine `src/clm/slides/translate_deck.py` (17 tests). Phase 2 = file/orchestration layer `src/clm/slides/translate_bootstrap.py` (19 tests) — the D2 dispatch (twin absent→bootstrap+mint+watermark; twin present→delegate to sync), all green; lint/format/mypy clean; full slides suite (1165) green. **Phase 3 (voiceover companion in lockstep) is next.**
**Branch:** `claude/slides-translate-bootstrap` off `master` (`fdf7772` handover, `cf1c8e06` Phase 1, Phase 2 commit follows). Do **not** branch off `claude/issue-226-partial-overlap-mismatch`.
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
- **Two deliberate deviations from the original phase plan** (see §8): (1) `TranslationCache` moved to **Phase 4** — a caching translator *wrapper* at the integration layer keeps Phase 1 pure/offline; (2) `slide_id` minting moved to **Phase 2** — the engine carries through whatever ids the source has (via `swap_lang`); minting a never-id'd source happens at the file/orchestration layer.

### Phase 2 — Idempotency + sync convergence + watermark — [DONE]
**Accomplished:** the D2 dispatch and the "next sync is a no-op" seal, in `src/clm/slides/translate_bootstrap.py`.

- `derive_bootstrap_paths(source_path, target_lang=None) -> BootstrapPaths`: resolves direction (`split_lang_tag`, with a `--to` override), derives the twin path **existence-agnostically** (`_twin_path` swaps the second-to-last dotted segment — `derive_split_twin` could not be reused because it returns `None` when the twin is *absent*, which is exactly the bootstrap case), and resolves all paths so the watermark key matches a later `clm slides sync`. Rejects a bilingual stem (no tag), a `voiceover_*` source, and a contradictory/unsupported `--to`. `twin_exists` folds in **non-emptiness** (an empty twin is treated as absent).
- `bootstrap_deck(...)`: the dispatch. **Twin present** (and not `--force`) → `_delegate_to_sync` = `build_sync_plan` + `apply_plan` mirroring `slides_sync_cmd`. **Twin absent / empty / `--force`** → `_bootstrap_new_twin`: `translate_deck_text` → `write_text(newline="\n")` → `assign_ids_in_split_pair(de, en, AssignOptions(accept_content_derived=True))` (EN-authority parity; mints onto **both** halves if the source was id-less; `force=False` preserves any author id) → `_record_watermark`.
- Pure orchestration over **injected** deps (translator/judge/recoverer/verifier/caches) — builds no LLM client, loads no `.env`, closes no cache (the CLI owns those). Offline-testable through the `SlideTranslator`/`SyncJudge` protocols.
- **Result:** `BootstrapResult` (`action` = `"bootstrapped"`/`"synced"`, `deck`/`assign` or `plan`/`apply_result`, `ids_assigned`, `watermark_recorded`).
- **Acceptance — met:** `translate` twice → second run is a `sync` no-op (`action=="synced"`, `proposals==[]`, no errors, deck not doubled), proven for **both** an id'd and an **id-less** source (the latter rewrites both halves on run 1, so it exercises the post-mint watermark). Round-trip + parity hold on the written files, including a **trailing-asymmetric** deck (ends on a slide pair). 19 tests in `tests/slides/test_translate_bootstrap.py`.

### Phase 3 — Voiceover companion in lockstep — [TODO]
**Accomplishes:** D5.

- Detect `voiceover_<name>.<src>.py`; translate localized cells preserving `for_slide` / `vo_anchor`; write `voiceover_<name>.<tgt>.py` via `effective_write_layout` (`src/clm/slides/sidecar_layout.py`).
- **Acceptance:** companion `for_slide` parity holds against the translated deck (`validator._check_split_companion_for_slide_parity`); narration cells are translated, anchors preserved.

### Phase 4 — CLI command `clm slides translate` (+ `bootstrap` alias) — [TODO]
**Accomplishes:** user-facing surface.

- New module `src/clm/cli/commands/slides_translate.py`; clone the option skeleton from `slides_sync.py` and the simple-mutation skeleton from `split.py`:
  - `--to en|de`, `--dry-run` / `--explain` (side-effect-free: parse + classify + count translatable vs copied + show target path + id plan; skip LLM/.env where possible), `--json` (`_to_dict`: `cells_translated`, `cells_copied`, `target_path`, `ids_minted`, `deferred`), `--force`, `--provider` (env `CLM_SYNC_PROVIDER`), `--translation-model`, `--cache-dir` (env `CLM_CACHE_DIR`), `--no-cache`, `--no-env-file`.
- Reuse `has_openrouter_api_key` (`openrouter_client.py:71`), `build_openrouter_client`, `load_env_files` (`env_loading.py:45`), `resolve_cache_dir`. No key → **exit 1, write nothing** (a whole deck of untranslated placeholders is not useful — unlike sync's per-cell defer); the engine is all-or-nothing. On `TranslationError` it already raises (never half-writes).
- **`TranslationCache` lands here (moved from Phase 1):** add a `TranslationCache` to `src/clm/infrastructure/llm/cache.py` (one table in `clm-llm.sqlite`; key = content-hash + **model-folded** `prompt_version`; cache only successful results) and expose it as a `CachingSlideTranslator` that *wraps* a `SlideTranslator` and is passed into `translate_deck_text`. The engine stays cache-agnostic (depends only on the protocol), so this is purely additive.
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

- **Completed:** design + **Phase 1** (pure engine `translate_deck.py` + 17 tests, `cf1c8e06`) + **Phase 2** (orchestration `translate_bootstrap.py` + 19 tests). Both lint/format/mypy clean; full `tests/slides` suite (1165) green. Issue **#232** filed. Branch `claude/slides-translate-bootstrap` off master. The three user-facing forks are settled (D1, D4, D5).
- **In progress:** none — Phase 3 not started.
- **Blockers / open questions:** none blocking. D6 defaults stand unless the user objects. Confirm at Phase 4: whether `--to` should be required when the source half has **no** lang tags at all (currently: infer from filename, `--to` optional override — note `derive_bootstrap_paths` requires a `.de`/`.en` tag on the *source*, so a tag-less single half is already rejected with a "run split first" hint).
- **Tests:** Phases 1–2 fully covered (see §7). Phases 3–5 pending.

---

## 5. Next Steps

**Start Phase 3 — voiceover companion in lockstep (D5).** The deck-half engine (`translate_deck_text`) already translates lang-tagged narrative cells and copies neutral ones, and `bootstrap_deck` is the orchestration seam to hook into. A voiceover companion (`voiceover_<name>.<src>.py`) is *just* lang-tagged cells with no header macro, which `translate_deck_text` handles as-is. Plan:
1. In `bootstrap_deck` (twin-**absent** path), after writing the deck twin, detect the source companion next to the source half. Companion naming is `voiceover_*` and it carries the same `.de`/`.en` tag; resolve its placement with `effective_write_layout` (`src/clm/slides/sidecar_layout.py`) — it may live as a sibling or under a `voiceover/` subdir (topic-sidecar-subdirs, PR #218). Reuse the resolver (`resolve_companion`) rather than re-deriving the path.
2. Translate the companion's text with `translate_deck_text` (same translator) and write `voiceover_<name>.<tgt>.py` via the layout. **Preserve `for_slide` / `vo_anchor`** — these are cell metadata the engine copies verbatim (they are not `lang`-gated), so they should survive untouched; add a test asserting it.
3. `derive_bootstrap_paths` currently **rejects** a `voiceover_*` *source* (a companion is never a standalone translate target). Keep that — the companion is found and translated *from the deck*, not passed directly.
4. **Idempotency:** a companion already present must not be re-translated/doubled. Mirror the deck dispatch — if the target companion exists, skip (or, later, sync it). For Phase 3, skip-with-note is acceptable; full companion-sync can defer.

**Acceptance:** companion `for_slide` parity holds against the translated deck (`validator._check_split_companion_for_slide_parity`); narration cells are translated; `vo_anchor` preserved; re-running does not double the companion. Add tests to `tests/slides/test_translate_bootstrap.py` (or a new `test_*` file) driven by `StaticSlideTranslator`.

**Phase 2 is DONE** (`translate_bootstrap.py`): the twin dispatch, EN-authority minting and watermark seal are in place and the `translate`-twice no-op is proven (incl. id-less + asymmetric decks). Engine-level round-trip is guaranteed by Phase 1; Phase 2 additionally proves the on-disk pair round-trips and is idempotent.

**Setup:** worktree venv must be synced (`uv sync --extra all`) — a repo-root `.venv` silently resolves `clm` to the main repo (memory `feedback-worktree-venv-sync`). Run tests with `uv run python -m pytest …` (system Python has no pytest).

**Gotchas to watch (these silently corrupt the pair):**
- **Phase 1 already handles** the translate-vs-copy gate (`metadata.lang`, *not* `role_of` — a localized id-less code cell is `role_of()==None` but must translate), the header-macro/import rewrite (reused `split.py` regexes), and the `split(unify(de,en))==(de,en)` validity guard. Don't re-implement these in Phase 2.
- Emitting the twin id-less → forces a downstream cold-mint/adopt round (needs verifier + key). Mint ids at bootstrap via `assign_ids_in_split_pair`.
- Missing watermark seal → the next `sync` re-diffs and may re-propose. The `_record_watermark` call is load-bearing for D2.
- Program extension may be `.cpp`/`.java` etc.; the `.de`/`.en` tag sits immediately before the final extension. Use `split_lang_tag` (prefix-agnostic) for naming — don't assume `.py`.
- Line endings: write with `newline="\n"`; never line-string surgery — use `raw_cells` / `FileState`.

---

## 6. Key Files & Architecture

### New files
- **`src/clm/slides/translate_deck.py` — DONE (Phase 1).** The pure, offline bootstrap engine: `translate_deck_text(source_text, *, source_lang, target_lang, translator) -> TranslateDeckResult`. Parses a source half, classifies cells by `metadata.lang`, translates localized ones (code via `CODE_ROLE`), copies neutral ones verbatim, rewrites the header macro/import (reusing `split.py` regexes), self-checks the split/unify round-trip, returns target text. Protocol-driven (`SlideTranslator`) so it's offline-testable. **Also reusable for the Phase 3 voiceover companion** — companions are just lang-tagged cells with no header macro, which this engine already handles.
- **`tests/slides/test_translate_deck.py` — DONE (17 tests).**
- **`src/clm/slides/translate_bootstrap.py` — DONE (Phase 2).** File/orchestration layer: `derive_bootstrap_paths` (direction + existence-agnostic twin path + rejections), `bootstrap_deck` (the D2 dispatch), `_bootstrap_new_twin` (translate→write→`assign_ids_in_split_pair`→`_record_watermark`), `_delegate_to_sync` (mirror of `slides_sync_cmd`). Injected deps only (no client/`.env`/cache-close). `BootstrapResult`/`BootstrapPaths`/`TranslateBootstrapError` are the public surface. **This is the seam Phase 3's voiceover companion hooks into.**
- **`tests/slides/test_translate_bootstrap.py` — DONE (19 tests).**
- `src/clm/cli/commands/slides_translate.py` — the `clm slides translate` Click command (Phase 4).
- `TranslationCache` + `CachingSlideTranslator` in `src/clm/infrastructure/llm/cache.py` / `sync_translate.py` (Phase 4 — moved from Phase 1).
- Tests: `tests/cli/test_slides_translate.py` (CLI), companion/idempotency tests (Phases 2–4).

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
- **Phase 1 invariants — DONE (17 tests, all green):** the primary assertion is `split(unify(de, en)) == (de, en)` (`_assert_valid_pair`) plus content checks (right language survives, shared cells present, header swapped). Byte-exact `== other_half` is asserted only on *trailing-symmetric* decks (header-only or shared-cell-terminated) — see the trailing-blank note in §8. The strongest cases use a `_mirror_translator` built from the canonical split halves so a correct engine regenerates the other side's content exactly.
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

### Phase 1 discoveries (read before Phase 2)
- **The translate-vs-copy gate is `metadata.lang`, NOT `role_of`.** The original plan said "gate on `role_of(meta) is None`", but `role_of` returns `None` for a localized id-less code cell (`# %% lang="de"` with no `slide_id`) — which *must* be translated. So: `lang == source_lang` → translate; `lang is None` → copy verbatim. `role_of`/`cell_type` only select the prompt (`CODE_ROLE` → code prompt). This is encoded in `_translation_role` and the main loop.
- **`split` produces trailing-blank-asymmetric halves.** The cell that ends the bilingual source carries an extra EOF blank line that, after `split`, lands on **only one** half. So a generated half (the engine mirrors the *source* half's per-cell trailing blanks) does **not** in general byte-match the other half of an arbitrary bilingual deck — and that is correct: when bootstrapping there is no pre-existing other half to match. The honest invariant is the round-trip, not byte-equality. Byte-exact tests therefore use decks that end on a shared cell (symmetric) or are header-only. Phase 2 writes these halves to disk as-is; don't try to "fix" the asymmetry.
- **The round-trip guard verifies split-*validity*, not translation *fidelity*.** A translator that injects a *valid* `lang="en"` cell boundary produces an extra-but-valid cell that still round-trips (guard won't flag it); only a structurally-breaking injection (e.g. a no-lang cell appearing in one half) trips it. Fidelity is the LLM's job / a later verify pass, not the engine's.
- **The engine reuses `split.py`'s private header regexes** (`_HEADER_DE_RE` etc.) via `from clm.slides import split`. Deliberate (the handover's "don't duplicate the header grammar" rule). If a reviewer prefers, promote them to public names in `split.py` — but do not fork copies.
