# Mobile Deck Studio — authoring a course from a phone

> **Status:** design proposal (not yet implemented).
> **Date:** 2026-06-20.
> **Author:** design draft, worked through interactively.
> **Scope:** a new **Studio** view on the existing `clm serve` web app
> (`src/clm/web/`) plus a no-build PWA frontend under
> `src/clm/web/static/`. Reuses the slide parser
> (`clm.notebooks.slide_parser`), the byte-exact write-back engine
> (`clm.slides.sync_writeback.FileState`), the sync engine
> (`clm.slides.sync_plan` / `sync_apply`) and its watermark cache, the
> `export`/`course decks`/`slides search` command surfaces, and the existing
> job queue for builds. No changes to the `.py` percent format itself.

---

## 1. Problem statement

Decks are authored as bilingual `.py` percent-format files, edited in VS Code on
a desktop. That workflow is excellent at the desk and miserable on a phone:
viewing and editing `.py` cells on a mobile device is cumbersome, yet the user
increasingly wants to work on a course while away from the desk.

The goal is a feature that makes **viewing and full authoring of decks pleasant
from a phone**, with the desktop machine doing all the heavy lifting.

### What "offline" turned out to mean

The original framing was "particularly offline." Working through it, the primary
need is **away-from-desk**, not zero-connectivity: the desktop is reachable from
the phone over Tailscale. That reframes the design from a pure-offline git
round-trip into a **mobile web authoring surface served by the desktop** — which
is both more powerful (full authoring, live access to all of CLM's machinery)
and more tractable (the source of truth and every tool stay on the desktop).

Literal offline (no desktop reachable) is retained only as a **P4 read-only
cache** of the last-opened deck — a nice-to-have, not a parallel editing track.

---

## 2. Concept

A **Studio** view added to the existing `clm serve` FastAPI app. The phone runs
a mobile-first PWA; the desktop runs the server. The phone is a thin authoring +
control surface — it drives desktop operations (parse, write-back, sync,
translate, render) rather than reimplementing them.

```
Phone (PWA, mobile-first, installed to home screen)
   │  REST /api/decks/*   +   WS /ws (live updates)
   ▼  (Tailscale HTTPS:  https://<machine>.<tailnet>.ts.net)
Desktop  clm serve  (binds localhost; tailscale serve terminates TLS)
   ├─ deck read   ──► clm.notebooks.slide_parser.parse_cells
   ├─ deck write  ──► clm.slides.sync_writeback.FileState        (byte-exact, atomic)
   ├─ structural  ──► split/unify-grade serializer               (byte-exact untouched cells)
   ├─ propagate   ──► clm slides sync | translate | assign-ids   (server-side, streamed)
   ├─ render      ──► jupytext + Jinja, no kernel exec           (fast preview)
   └─ watch       ──► watchfiles → "deck changed on disk" over WS (VS Code race guard)
```

Why extend `clm serve` rather than add a new command: the FastAPI app, lifespan
wiring, WebSocket push, CORS/`--host`/`--reload`, and the `watchfiles` dependency
already exist in the `[web]` extra. The app currently exposes a Monitor view
(jobs/workers); Studio becomes a second view sharing the same server.

---

## 3. Settled design decisions

Each of these was an explicit fork resolved during design.

### 3.1 Delivery & frontend stack

- **No-build frontend:** Preact + htm with **vendored** CodeMirror 6, shipped as
  static files in `src/clm/web/static/` (no `static/` dir or JS toolchain exists
  today — this is greenfield). No npm/Node in CI or release; the wheel ships the
  assets; everything is local so the PWA works offline.
  - Rationale: the user-facing editing quality is set by CodeMirror, **not** by
    whether there is a build step. The only build-sensitive user-visible thing is
    first-load size, which is absorbed by LAN/Tailscale delivery + PWA caching.
    A Node toolchain would tax a Python-first, Windows-first repo (CI, release,
    every contributor and AI agent) for a complexity problem this modest app does
    not have. Preact + htm gives components + reactivity without JSX/build.
  - **Migration path:** Preact-no-build → Preact-with-Vite is the same library,
    so adding a build step later (if the UI outgrows no-build) is incremental, not
    a rewrite. Not locked in.

### 3.2 Serving, exposure, auth

- **Behind Tailscale HTTPS.** `clm serve` binds `localhost`; `tailscale serve`
  terminates TLS at `https://<machine>.<tailnet>.ts.net`. This yields a real,
  trusted certificate → a **secure context**, which is required for the PWA's
  service worker (P4 offline cache) and "install to home screen." `clm serve` can
  detect whether `tailscale serve` is running and offer to start it.
  - A raw `http://100.x.x.x` Tailscale IP is **not** a secure context, so plain
    HTTP would forfeit install + offline. Self-signed TLS triggers cert-trust
    warnings and can block service workers. Tailscale HTTPS avoids both.
- **Tailscale-only by default.** Never silently bind `0.0.0.0`; LAN exposure is
  an explicit `--host` opt-in.
- **Pairing via QR + bearer token.** On launch, `clm serve` prints a **QR code in
  the terminal** (pure-Python, e.g. `segno` — no system deps) encoding the URL +
  a **persistent bearer token** (stored in the user config dir so the QR is
  stable across restarts; `--rotate-token` cycles it). Scanning opens the PWA
  already authenticated; the app stores the token (localStorage) and sends it on
  every API/WS call. One token, full access — anyone on the tailnet could reach
  the URL, so the token is the real gate.

### 3.3 Scope & navigation

- **One course/spec per `clm serve` instance** (a spec + its slides dir; run a
  second instance for a second course). Simplest, clearest scoping.
- **Primary surface: Recents + search.** The phone lands on recently-edited decks
  with a prominent search box (reusing `clm slides search` over titles + cell
  text). The spec-resolved **tree** (sections → topics → decks, with
  present/missing/orphan + language-coverage badges, from `clm course decks`) is
  one tap away. A **"Not in spec"** bucket (from `clm course orphans`) keeps
  drafts reachable but visibly distinct.
  - Rationale: thumb-driven navigation of a deep tree is painful; "get me back to
    what I was doing" dominates mobile use. The tree remains available for
    surveying the course.

### 3.4 Editing model

- **Full authoring** — markdown, code, and structural insert/delete/move — via
  **cell-level operations**, never a whole-file PUT (a whole-file write from a
  phone is the riskiest thing given concurrent VS Code editing).
- Op vocabulary: `edit-body`, `edit-tags`, `insert`, `delete`, `move`.

### 3.5 Bilingual model — single-source, either language

Decks are bilingual interleaved `.py` (`lang="de"` / `lang="en"` cells; code
cells are language-neutral and shared).

- **Single source, either language.** You author in one language; the other is
  derived. *Which* language is the source is a per-deck choice, not a global
  constant: the **first edit in a session picks the source half, and the other
  half is locked** for the rest of the session.
- **The lock is derived from the sync watermark, not invented session state:**

  > A language is editable iff the *other* half is **clean** relative to the last
  > synced baseline (the watermark).

  Both halves clean → either is editable; first edit dirties one half → the other
  is locked; after a **clean sync** both are clean again → the lock releases and
  either language may be chosen next. This reuses exactly what `suggest-sync` +
  the watermark cache already compute, and it ties the lock to data-consistency
  state (survives app reloads and second devices) rather than to a browser
  session.
- **Why it stays cheap:** locking the non-source half guarantees only one side is
  ever dirty within a session, so propagation is always a clean one-directional
  sync (edited → stale) — never the both-sides-changed conflict that makes full
  bidirectional editing expensive. Backend cost is close to the fixed-source
  option.
- **Escape hatch:** a **Discard & unlock** action reverts the in-session edits,
  restoring the clean state and releasing the lock (for "I started editing German
  but changed my mind").
- **On-demand propagation.** Edits save to the source half instantly; the other
  half is **marked stale**, not auto-synced. A **"Sync to other language"** action
  runs `clm slides sync` / `translate` / `assign-ids` **server-side** (where the
  LLM access and watermark live), streamed over WS. New slides mint a `slide_id`
  and get a translation placeholder until synced.
  - Rationale: firing the LLM on every keystroke is slow and costly on the move;
    edit-now/propagate-later matches CLM's existing edit → stale → reconcile model.
- **Caveat:** the lock governs *in-app* edits only. A concurrent VS Code edit on
  the desktop could dirty the other half behind the lock's back; that cross-tool
  case is caught by the concurrency guard (§3.6), with the sync conflict path as
  the backstop.

### 3.6 Concurrency safety — the keystone

The recurring CLM failure mode is two editors on one file. Here the phone and VS
Code may both touch the same `.py`. Three mechanisms close the race:

1. **Optimistic concurrency on every write.** A read returns each cell's
   `content_hash` (the sync engine's `anchor_of`/content hash, already computed)
   and a deck-level `deck_version` (hash of the whole file — cheap at these
   sizes). A write (`PATCH /api/decks/{id}/cells/{slide_id}`) carries
   `expected_deck_version` + `expected_cell_hash`. The server validates in order:
   - `deck_version` mismatch → **409** (something — likely VS Code — rewrote the
     file); phone re-fetches before retrying.
   - `cell_hash` mismatch → **409**.
   - target language locked per watermark → **423 Locked**.
   Only then does `FileState` write back. A phone write can **never silently
   clobber** a desktop edit.
2. **Atomic, byte-exact write-back.** `FileState` looks up cells by
   `(slide_id, role)` (not line number), edits in place, and persists atomically
   via swap, leaving untouched cell headers/padding verbatim.
3. **External-change watcher.** `watchfiles` watches the slides dir; when an open
   deck changes on disk the server pushes `deck-changed-on-disk` over WS and the
   phone shows "changed on disk — reload," disabling Save until reload. This
   closes the fetch→write gap for the VS Code case.

### 3.7 Structural ops without byte-drift

`delete` is `FileState.delete_cell`. `insert` / `move` must emit new cell text;
the risk is whitespace that differs from CLM's canonical form, surfacing later as
spurious DE/EN divergence (CLM has burned on `lines_to_next_cell`/whitespace
before). Mitigation: route structural writes through the **same serializer that
backs `split`/`unify`** (byte-identical round-trip, already tested), and mirror
that test pattern — assert untouched cells are byte-for-byte unchanged after any
structural op. No naïve whole-file re-emit.

### 3.8 Preview — three tiers, cheapest first

1. **Client-side markdown (instant, per-cell).** The Edit/Preview toggle renders
   the cell body as CommonMark in-browser, no round-trip. Cannot expand Jinja,
   resolve cross-refs, run code, or draw diagrams.
2. **Server-side render, no execution (fast, debounced).** `is_j2` cells (header
   macros, etc.) are rendered server-side through jupytext + Jinja **without
   kernel execution**, so macros/headers show expanded rather than raw `{{ }}`.
   Over Tailscale this round-trip is fast and the editor debounces it.
3. **Full executed build (slow, true output).** A real single-deck build via the
   job queue (kernel exec / replay, PlantUML/drawio). **Deferred** — not in the
   initial phase plan.

- **Inline toggle = hybrid:** tier 1 for plain markdown, tier 2 for `is_j2` cells.
- **"Build preview" button = tier 2 (fast no-exec) only, for now.** The full
  executed build (tier 3) is left for a later phase.

---

## 4. Phase plan

| Phase | Delivers |
|---|---|
| **P0** | `clm serve` **Studio view** + Tailscale-HTTPS / QR / token auth scaffold; navigation (Recents, search, tree with badges, "Not in spec"); open a deck; **read-only** cell render (client markdown + server-side `is_j2` render); full-text search. Reuses `parse_cells`, `course decks`/`orphans`, `slides search`. No writes. |
| **P1** | **Cell body/tag editing** + the **concurrency core**: optimistic `deck_version` + `cell_hash` (409/423), atomic `FileState` write-back, `watchfiles` external-change watcher, autosave + 409/disk-change UX. The safety keystone — built and tested first. |
| **P2** | **Structural ops** (`insert` / `delete` / `move` / mint-id) via the byte-exact serializer; reorder mode in the UI; byte-exact untouched-cell tests. |
| **P3** | **Bilingual**: language-view + watermark-derived lock + Discard/unlock + **Sync-to-other-language** (server-side `sync` / `translate` / `assign-ids`, streamed over WS); stale badges. |
| **P4** | **Build-preview** (tier-2 no-exec deck render streamed over WS) + installable PWA + **read-only offline cache** of the last-opened deck. |
| **Later / optional** | **Full executed single-deck build** (tier 3) for code outputs and rendered diagrams. |

---

## 5. Frontend UI sketch

- **Deck screen** — top bar: deck title · language toggle with lock state
  (`DE ⚫ / EN 🔒`) · stale-count badge · overflow menu (Sync to other language ·
  Build preview · Discard & unlock · deck info). Body: a vertical list of cell
  cards (rendered markdown / highlighted code, role + tag chips, inline **+** to
  insert). Tap a card to edit.
- **Edit sheet (full-screen)** — markdown cells open CodeMirror in markdown mode
  with an **Edit / Preview** toggle and a **formatting toolbar docked above the
  keyboard** (heading, bold, list, code, link — phone keyboards lack these). Code
  cells open in Python mode, monospace (code edits expected to be rare on a
  phone).
- **Reorder mode** — a toggle that swaps tap-to-edit for up/down chevrons, instead
  of fiddly inline drag on a touchscreen.
- **Save** — autosave on close with an explicit saved/failed indicator; on `409`,
  inline "changed elsewhere — reload."
- **Status banners** — lock banner ("English locked — German is the active source;
  sync or discard to unlock") and watcher banner ("changed on disk — reload").

---

## 6. Backend surface (sketch)

REST under `/api/decks` (course pre-scoped per server instance):

| Method & path | Purpose |
|---|---|
| `GET /api/decks` | Tree + recents + "not in spec"; status/coverage badges. |
| `GET /api/decks/search?q=` | Full-text search (reuses `slides search`). |
| `GET /api/decks/{id}?lang=` | Cells (with `content_hash`), `deck_version`, lock state. |
| `POST /api/decks/{id}/render-cell` | Tier-2 no-exec render of one `is_j2` cell. |
| `PATCH /api/decks/{id}/cells/{slide_id}` | `edit-body` / `edit-tags` (optimistic). |
| `POST /api/decks/{id}/cells` | `insert` (mints id). |
| `DELETE /api/decks/{id}/cells/{slide_id}` | `delete`. |
| `POST /api/decks/{id}/reorder` | `move`. |
| `POST /api/decks/{id}/sync` | Propagate source → other language (streamed via WS). |
| `POST /api/decks/{id}/discard` | Discard in-session edits, unlock. |
| `POST /api/decks/{id}/preview` | Tier-2 no-exec deck render (P4). |

WS `/ws` events: `deck-updated` (after any write, for other tabs),
`deck-changed-on-disk` (watcher), `sync-progress` / `preview-progress`.

Auth: bearer token on every REST + WS call; `423` when the language is locked.

---

## 7. Top risks

- **The two-editor race** (phone + VS Code on one `.py`) — mitigated by §3.6, but
  it is CLM's recurring failure mode and needs careful tests in P1.
- **Structural re-serialization drift** (§3.7) — must preserve byte-exact
  untouched cells; lean on the `split`/`unify` writer, not a naïve re-emit.
- **Mobile code-cell ergonomics** — typing Python on a phone is painful; optimize
  for markdown/notes, accept that code edits are rare.
- **Secure-context dependency on Tailscale HTTPS** — install + offline require it;
  document the one-time `tailscale serve` / tailnet HTTPS setup clearly.

---

## 8. Reuse map

| Need | Module / command |
|---|---|
| Web app, WS, lifespan, CORS, `watchfiles` | `clm.web.app`, `clm.web.api.*` (`[web]` extra) |
| Parse `.py` cells | `clm.notebooks.slide_parser.parse_cells` |
| Byte-exact write-back | `clm.slides.sync_writeback.FileState` (`find_cell`/`replace_cell_body`/`replace_cell_tags`/`delete_cell`) |
| Content identity / hashes | `clm.slides.sync_writeback.anchor_of` |
| Propagation | `clm slides sync` (`sync_plan` / `sync_apply`), `translate`, `assign-ids` |
| Lock / stale derivation | `SyncWatermarkCache`, `clm slides suggest-sync` |
| Navigation / status | `clm course decks` / `orphans`, `clm slides search` |
| Byte-exact serializer for structural ops | the `split` / `unify` writer path |
| Export-style rendering patterns | `clm.cli.commands.export` + `_export_shared` |
