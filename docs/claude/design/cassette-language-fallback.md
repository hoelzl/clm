# Cassette Language Fallback for Split `.de`/`.en` Decks (Issue #159)

> **Status:** design proposal (not yet implemented).
> **Issue:** [#159](https://github.com/hoelzl/clm/issues/159) — *HTTP-replay:
> fall back from language-specific (`.de`/`.en`) cassette name to the base
> cassette.*
> **Author:** design draft, 2026-05-30.
> **Scope:** cassette **resolution** in
> `src/clm/core/course_files/notebook_file.py` plus a *replay-only* wiring
> change in `src/clm/core/operations/process_notebook.py`. Independent of the
> #158 (1.7) milestone; can ship standalone (consumers pin clm by commit SHA).
> **Consumer:** hoelzl/PythonCourses DE/EN language-split conversion.

> **Grounding.** Line numbers verified against the working tree on 2026-05-30
> (they differ from those in the issue, which has drifted). Key code:
> `notebook_file.py:91` (`cassette_path`), `:110` (`expected_cassette_path`),
> `:129` (`cassette_relative_name`), `:140` (`expected_cassette_relative_name`),
> `:60-66` (`output_language_filter`); the replay/record switch
> `_resolve_cassette_name` and `compute_other_files` in
> `core/operations/process_notebook.py:82` / `:105`; the orphan-staging sweep
> `course.py:373` (esp. `:427`); the worker record path —
> `_resolve_cassette_paths` (`notebook_processor.py:1815`),
> `seed_staging_from_canonical` (`http_replay_cassette.py:80`),
> `merge_staging_into_canonical` (`:119`); `allow_playback_repeats=True` in the
> bootstrap (`notebook_processor.py:359`).

---

## 1. Problem statement

CLM derives each slide deck's HTTP-replay cassette name from the slide file's
`Path.stem`. `cassette_path` (`notebook_file.py:91`) tries a nested then a
sibling cassette and returns `None` if neither exists:

```python
@property
def cassette_path(self) -> Path | None:
    stem = self.path.stem
    cassette_name = f"{stem}.http-cassette.yaml"
    topic_dir = self.path.parent
    nested = topic_dir / "_cassettes" / cassette_name
    if nested.exists():
        return nested
    sibling = topic_dir / cassette_name
    if sibling.exists():
        return sibling
    return None
```

When a **bilingual** deck is converted to the **split** DE/EN format the
filenames change, and the cassette name derived from them changes too:

| Source file | `Path.stem` | Cassette name looked up |
|---|---|---|
| `slides_010_prompt_templates.py` (bilingual) | `slides_010_prompt_templates` | `slides_010_prompt_templates.http-cassette.yaml` |
| `slides_010_prompt_templates.de.py` (split) | `slides_010_prompt_templates.de` | `slides_010_prompt_templates.de.http-cassette.yaml` |
| `slides_010_prompt_templates.en.py` (split) | `slides_010_prompt_templates.en` | `slides_010_prompt_templates.en.http-cassette.yaml` |

After the split, the new decks look for `…​.de.http-cassette.yaml` /
`…​.en.http-cassette.yaml`, **which do not exist**, so `cassette_path` returns
`None` and there is **no fallback to the base name**. The recorded
interactions are stranded even though they sit right there on disk.

Two facts make this especially wasteful:

1. **The base (bilingual) cassette already holds both languages'
   interactions.** A single bilingual build of `prompt_templates` recorded
   *15 DE + 16 EN* chat-completion entries into one file.
2. **The base cassette safely outlives its source `.py`.** The only sweep that
   exists, `_sweep_orphan_cassette_staging_files` (`course.py:373`), merges
   orphan `*.http-cassette.yaml.staging-*` files into canonical and **never
   deletes a canonical cassette whose source `.py` is gone**. So when the
   bilingual `.py` is deleted during the split, its cassette persists and can
   serve as the fallback target.

Without a fallback, splitting an LLM deck forces either **copying** the
bilingual cassette to both names (2× bloat + stale cross-language entries) or
**re-recording** per language (API cost + reintroduced nondeterminism). A
lookup fallback avoids both.

---

## 2. How replay vs. record is wired (verified) — and why this is subtle

The build resolves a cassette name per output via `_resolve_cassette_name`
(`process_notebook.py:82`):

```python
mode = self.http_replay_mode
if not mode or mode == "disabled":
    return None
if mode == "replay":
    return self.input_file.cassette_relative_name          # (A) replay
# once / new-episodes / refresh — record-capable.
existing = self.input_file.cassette_relative_name          # (B) record, reuse-if-present
if existing is not None:
    return existing
return self.input_file.expected_cassette_relative_name     # (C) record, fresh target
```

and `compute_other_files` (`:105`) ships the cassette bytes to the worker:

```python
if self.http_replay_mode and self.http_replay_mode != "disabled":
    cassette = self.input_file.cassette_path               # (D)
    cassette_name = self.input_file.cassette_relative_name # (D)
    if cassette is not None and cassette_name is not None:
        other_files[cassette_name] = b64encode(cassette.read_bytes())
```

In record-capable modes the worker then **seeds** its staging file from that
canonical (`seed_staging_from_canonical`, `notebook_processor.py:2017`),
records, and **merges** staging back into canonical
(`merge_staging_into_canonical`).

**The subtlety that the issue misses:** `cassette_relative_name` (and
`cassette_path`) are read at **(A) replay, (B) record-reuse, and (D) shipping**
— and `cassette_path` is *also* read by the sweep (§4). Only **(A)** wants the
language fallback. If the fallback is added to the shared
`cassette_path`/`cassette_relative_name`, it leaks into (B), (D), and the
sweep, with the failure modes in §3.2/§4.

Other relevant facts:

| Concern | Detail | Code |
|---|---|---|
| Body matching | replay matches on the normalized request **body** (#126) → order- and language-independent | issue #159 |
| Repeats | `allow_playback_repeats=True` → an entry is not depleted when requested more than once (#95) | `notebook_processor.py:359` |
| Split routing | `.de.py`/`.en.py` files carry `output_language_filter = "de"/"en"` (set by `Topic.add_file`); the build emits only that language for that file | `notebook_file.py:60-66` |

---

## 3. Proposed design

### 3.1 Core change — a language fallback used by the replay branch only

For a slide file whose stem ends in a single trailing `.de`/`.en` token,
replay lookup should:

1. Try the **language-specific** cassette: `<stem>.http-cassette.yaml`.
2. If absent, fall back to the **base** cassette:
   `<stem-without-.de/.en>.http-cassette.yaml`.
3. Apply both attempts in **both** layouts — nested `_cassettes/<name>` and
   sibling `<topic_dir>/<name>` — preserving the existing **nested-preferred**
   order.

Resolution order for `slides_010_prompt_templates.de.py` (first existing wins):

```
1. _cassettes/slides_010_prompt_templates.de.http-cassette.yaml   (lang, nested)
2. <topic_dir>/slides_010_prompt_templates.de.http-cassette.yaml  (lang, sibling)
3. _cassettes/slides_010_prompt_templates.http-cassette.yaml      (base, nested)
4. <topic_dir>/slides_010_prompt_templates.http-cassette.yaml     (base, sibling)
```

That is: **language-specific name wins across layouts; nested wins over sibling
within each tier.** (Ordering decision in §7.1.) `.en` is symmetric.

### 3.2 Wire the fallback into the `replay` branch only — not the shared property

Because `cassette_relative_name` is consulted in record modes too (§2, path B)
and when shipping bytes (path D), the fallback must **not** be added to the
shared `cassette_path`/`cassette_relative_name`. Instead:

- Add **new, fallback-enabled** resolvers: `replay_cassette_path` (strict
  `cassette_path`, else base-name search across both layouts) and
  `replay_cassette_relative_name` (its kernel-relative form).
- Use them **only** in the `replay` branch of `_resolve_cassette_name`
  (`process_notebook.py:98`, path A) and in the replay case of
  `compute_other_files` (path D, so the base bytes are shipped under the base
  name the worker will look up).
- Leave `cassette_path` / `cassette_relative_name` **strict** so the
  record-reuse branch (B), the sweep (§4), and the record-seed all keep using
  the exact language-specific name.

This is the minimal change that keeps the fallback confined to *strict replay*.

### 3.3 What each mode does (deliberate matrix)

For a split `.de` deck with **only the base cassette** on disk:

| `--http-replay` mode | Behavior | Why |
|---|---|---|
| `replay` (strict, CI default) | **Replay from the base** (fallback, path A). No live calls. | The whole point of #159. |
| `once` | No `.de` cassette (strict) → **record a fresh `.de`** (seeded empty, path C). | `once` = "record when absent"; produces a clean, minimal `.de` cassette. |
| `refresh` | **Re-record a fresh `.de`.** | refresh always re-records. |
| `disabled`/None | no cassette activity | — |

The record-capable modes deliberately **do not** reuse the base. Doing so would
require seeding `.de` staging from the base, which carries the **EN** entries
into the merged `.de` canonical (cross-language bleed — see §3.4). Confining the
fallback to `replay` sidesteps that entirely: `replay` reuses the base
read-only; `once`/`refresh` produce clean per-language cassettes.

### 3.4 Split-brain / cross-language guard (why the scoping matters)

If the fallback leaked into record (paths B/C/D), two concrete corruptions
follow:

1. **Overwrite the shared base.** If a record run resolved its write target to
   the base, `merge_staging_into_canonical` would write one language's
   interactions onto the shared base and **destroy the other language's
   entries**.
2. **Cross-language bleed via seeding.** `seed_staging_from_canonical`
   (`notebook_processor.py:2017`) copies the resolved canonical into staging
   before recording. If that canonical were the base, the merged `.de` cassette
   would **inherit the EN entries** — no longer the clean per-language cassette
   the issue wants.

§3.2's replay-only scoping prevents both by construction.

### 3.5 Stem-stripping rule

- Strip only a **single** trailing `.de`/`.en` token, immediately before the
  already-removed `.py`. `…templates.de` → `…templates`.
- No-token stems are unchanged → behavior identical to today.
- The token must be **exactly** `de` or `en`. `slides_010_v1.2` is untouched.
- Strip at most once.

A helper `_base_cassette_stem(stem) -> str | None` returns the base stem when a
language token is present, else `None`.

---

## 4. Critical interaction the issue overlooks: the orphan-staging sweep

`_sweep_orphan_cassette_staging_files` (`course.py:373`) computes each
notebook's canonical from `cassette_path`:

```python
canonical = file.cassette_path or file.expected_cassette_path   # course.py:427
...
staging_glob = f"{canonical.name}.staging-*"                     # course.py:451
```

If the fallback lived **inside `cassette_path`**, a split `.de` file with only
a base cassette would resolve `canonical = base`, so the sweep would glob for
`<base>.staging-*` and:

1. **Strand `.de` staging.** A crashed/partial `.de` record leaves
   `<stem>.de.http-cassette.yaml.staging-*`; the file's canonical now points at
   the base, so its glob misses the `.de` staging → never swept/merged, never
   cleaned. Regression.
2. **Open a base-mutation surface.** The split file could fold base-named
   staging into the shared base on behalf of one language.

The §3.2 scoping (fallback only in `replay_cassette_*`, `cassette_path` left
strict) keeps the sweep on the exact language-specific name — **zero**
behavioral change. (If instead the fallback were added to `cassette_path`,
`course.py:427` would have to switch to `file.expected_cassette_path`.) Cover
this with a test (§9).

---

## 5. The scenario the issue calls out: a split deck gains new HTTP calls

> *"What happens when a split `.de`/`.en` notebook currently replaying a
> unified [base] cassette is modified to include new HTTP calls?"*

"New HTTP calls" = a brand-new LLM call or an **edited prompt** (changed body →
the body matcher treats it as new).

### 5.1 `replay` mode (strict)

- **Unchanged calls** still match base entries via the fallback → replay fine.
- **The new/changed call** has no matching body in the base → strict replay
  raises (vcrpy `CannotOverwriteExistingCassetteException`).

This is **correct and desirable**: the cassette is genuinely out of date for
this deck; strict replay should fail loudly, not go live. The fallback makes
this *strictly better* than today — without it the deck has *no* cassette and
fails on its first call; with it, only the *truly new* call fails.

**Recommendation:** when replay resolved via the base (fallback) tier, say so
in the error — *"replaying base cassette `…prompt_templates.http-cassette.yaml`
for split deck `…​.de.py`; no match for this request (prompt added or changed)
— re-record this deck (`--http-replay=refresh`)."* Turns "corrupt cassette"
into an actionable, local signal.

### 5.2 `once` / `refresh` (the remediation)

Per §3.3 these are **strict** for split files, so a re-record:

- Resolves the write target to the language-specific `.de` (path C; the strict
  `cassette_relative_name` is `None` because no `.de` exists yet).
- Seeds from the **empty** `.de` canonical (not the base — §3.4), records the
  modified `.de` deck's calls, and merges into `.de`.
- Produces a **clean, self-contained** `…prompt_templates.de.http-cassette.yaml`
  (old + new, no EN bleed) that shadows the base on the next replay (§3.1 tier
  1).
- Leaves the base untouched, **still serving `.en`** via fallback — the two
  split files are independent, so editing `.de.py` never invalidates `.en`.

Convergence: edit a language → re-record that language → the deck migrates from
"shared base" to "per-language," one language at a time, with no effect on the
other.

### 5.3 Edge cases within this scenario

1. **Only `.de` gains a call; `.en` unchanged.** Independent files; only `.de`
   re-records and shadows; `.en` keeps the base via fallback. No
   cross-contamination.
2. **A stale `.de` cassette already exists.** Tier 1 shadows the base → stale
   `.de` used → new call misses under strict replay → fail loudly →
   `--http-replay=refresh` regenerates it. Footgun: a narrow stale `.de`
   shadows a broader base; escape hatch is to **delete the `.de` cassette** to
   fall back to base again. Candidate `clm cassette doctor` warning.
3. **The new call is language-agnostic.** Recorded under whichever language
   re-records first; the other keeps matching the base until it re-records too.
   No wrong replay; at worst a duplicate recording.

---

## 6. Other situations that may arise

### 6.1 Born-split deck (never bilingual)
No base ever existed → fallback finds nothing → `None` → behaves exactly as
today in every mode.

### 6.2 Re-unify (split → bilingual again)
The re-unified stem has no token → resolves the base name directly; the base
still serves it. Leftover `.de`/`.en` cassettes become harmless orphans (shadow
nothing); clean up manually or via a future doctor pass.

### 6.3 Both languages re-recorded
Once both per-language cassettes are complete, the base is fully superseded —
safe to delete manually; doctor could report "base `X` superseded by
`X.de` + `X.en`."

### 6.4 Three or more languages / other tokens
Token-specific (`de`/`en` today). Adding a language is a one-line extension of
the recognized-token set; resolver structure unchanged.

### 6.5 Do **not** add an orphan-*canonical* sweep
The fallback depends on the base **surviving** the deletion of its bilingual
`.py`. The existing sweep already leaves canonical files alone — keep it that
way. A future orphan-canonical sweep **must** exclude base cassettes still
reachable via fallback from any split deck (or be strictly opt-in), or it will
silently re-strand split decks.

### 6.6 Interaction with `clm cassette doctor` (#156)
Doctor operates per cassette file (`workers/notebook/cassette_doctor.py`,
`iter_cassette_paths`). With the fallback a split deck *reads* a shared base it
does not exclusively own. Doctor must **not** mutate a base cassette on behalf
of one language (it could corrupt the other language's entries); when a deck
resolves via fallback, treat the base as read-only/shared and skip or restrict
repair.

### 6.7 Docker / relative paths
The base cassette lives in the *same* topic dir (nested or sibling) as the `.de`
source, just a different filename, so `replay_cassette_relative_name` (computed
relative to the topic dir) stays valid in both direct and Docker modes.

---

## 7. Open questions / decision points

### 7.1 Precedence ordering across layouts
§3.1 proposes **name-tier-first** (language across both layouts, then base
across both). Alternative: **layout-first**. Name-tier-first is recommended so
"language-specific shadows base" holds unconditionally; confirm against how
PythonCourses actually lays out `_cassettes/` vs siblings.

### 7.2 Should `once` also reuse the base?
§3.3 makes `once` record fresh per-language (safe, but costs live calls when
only a base exists). Reusing the base in `once` would need the seed to be
**language-filtered** (drop the other language's interactions before merge) to
avoid the §3.4 bleed. Deferred; `replay`-only fallback covers the CI/strict
path, which is the stated need.

### 7.3 Diagnostics
Emit an INFO log when a deck replays via the base fallback ("deck `X.de`
replaying base cassette `X`") so operators see which decks have converged to
per-language vs. still ride the base. Recommended: yes, once per deck.

---

## 8. Implementation sketch

- `notebook_file.py`
  - Add module-level `_base_cassette_stem(stem) -> str | None` (§3.5).
  - Add `replay_cassette_path` (property): return `self.cassette_path` if
    non-`None`; else, if `_base_cassette_stem(self.path.stem)` is non-`None`,
    search `_cassettes/<base>.http-cassette.yaml` then
    `<topic_dir>/<base>.http-cassette.yaml`; else `None`.
  - Add `replay_cassette_relative_name` (mirrors `cassette_relative_name` but
    over `replay_cassette_path`).
  - Leave `cassette_path`, `cassette_relative_name`, `expected_cassette_path`,
    `expected_cassette_relative_name` **unchanged**.
- `core/operations/process_notebook.py`
  - `_resolve_cassette_name` (`:97-98`): in the `replay` branch return
    `self.input_file.replay_cassette_relative_name`. Leave the record branch
    (`:100-103`) on the strict properties.
  - `compute_other_files` (`:137-141`): when `mode == "replay"`, ship
    `replay_cassette_path` bytes under `replay_cassette_relative_name`;
    otherwise keep the strict properties.
- `course.py`: **no change** (sweep keeps strict `cassette_path`).
- No change to the worker matcher, record modes, repeats, or seed/merge.

---

## 9. Acceptance criteria / tests

1. **Unit — `replay_cassette_path` for `slides_NNN.de.py`:** returns the `.de.`
   cassette when present; else the base; else `None`. Same for `.en`. Exercise
   the nested `_cassettes/` layout. Non-split stems unchanged.
2. **Unit — strict properties unchanged:** `cassette_path` /
   `cassette_relative_name` still return `None` for a `.de` file when only a
   base cassette exists (so record-reuse, shipping, and the sweep stay
   language-specific).
3. **Unit — precedence:** with both base and `.de` present, `.de` wins (§3.1).
4. **Unit — stem rule:** no-token stems untouched; single-token strip only.
5. **Unit — sweep canonical (§4):** the sweep resolves a split `.de` file's
   canonical to the language-specific name (not the base) when only a base
   cassette is on disk; `.de` staging is still swept.
6. **Integration — strict-replay round trip:** build a split `*.de.py` +
   `*.en.py` pair with **only** the base bilingual cassette under
   `--http-replay=replay`; assert no live calls and no
   `CannotOverwriteExistingCassetteException`.
7. **Integration — `once`/`refresh` produces clean per-language (§3.3/§5.2):**
   re-record a split `.de` deck that has only a base on disk; assert the new
   `.de` cassette contains only `.de` interactions (no EN bleed) and the base
   is byte-unchanged.
8. **Integration — new-call scenario (§5):** add a call to the `.de` deck;
   assert (a) strict replay fails on exactly that call with a base-aware
   message, and (b) a refresh run yields a complete, self-contained `.de`
   cassette that shadows the base and leaves `.en` still served by the base.
9. **Regression:** non-`http_replay` topics and born-split decks unaffected.
10. **Docs:** record the fallback + precedence in the cassette/architecture
    docs; per CLAUDE.md's Info Topics rule, reflect any user-visible behavior
    in `src/clm/cli/info_topics/` if a flag/command is added.

---

## 10. Summary

The bug is one of **cassette name resolution**: split `.de`/`.en` decks derive
a language-specific cassette name that does not exist, with no fallback to the
base bilingual cassette that already holds both languages' interactions.

The fix is a **read-only, copy-free language fallback wired into the `replay`
branch only** — language-specific name first, base name second, across both
layouts — via new `replay_cassette_path` / `replay_cassette_relative_name`
properties, leaving the strict `cassette_path` / `expected_cassette_path`
untouched.

The non-obvious part — and the reason "just add a fallback to `cassette_path`"
is wrong — is that `cassette_path`/`cassette_relative_name` are read by **four**
call sites, only one of which (strict `replay`) should see the fallback:

1. **record-reuse** (`process_notebook.py:100`) — adding the fallback here makes
   `once`/`refresh` resolve to the base and **pollute it** on merge;
2. **byte shipping** (`:138-139`) — would ship the base under the base name in
   record modes;
3. **the orphan-staging sweep** (`course.py:427`) — would mis-attribute and
   **strand `.de`/`.en` staging** (§4);
4. **strict replay** (`:98`) — the one site that wants it.

Scoping the fallback to (4) keeps replay correct (body matching #126 +
`allow_playback_repeats` #95 mean DE matches DE and EN matches EN with no
depletion) while leaving record, shipping, and the sweep strictly
language-specific. The "split deck gains a new HTTP call" scenario then degrades
gracefully: unchanged calls keep replaying, the new call fails loudly under
strict replay (better than today), and a single-language `refresh` produces a
clean per-language cassette without touching the other language (§5).
