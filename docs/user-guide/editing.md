# Mobile Deck Editing

CLM includes a mobile-friendly deck editor — an offline, LAN-served web UI
for editing percent-format slide files (`.py`, `.cpp`, `.cs`, `.java`,
`.ts`) from a browser. It is designed for the situation where you want to
work on a course from your phone: run `clm edit` on your desktop, open the
printed URL on your phone over the same Wi-Fi, and edit slides on the go.

## Installation

```bash
# Install CLM with the editor
pip install -e ".[edit]"
```

The `[edit]` extra adds Jinja2 and python-multipart on top of CLM's core
FastAPI/uvicorn stack. No external tools are required.

## Starting the editor

```bash
# Edit the course in the current directory (must contain slides/)
clm edit

# Point at a specific course and expose it on the LAN for your phone
clm edit --data-dir /path/to/course --host 0.0.0.0

# Custom port, don't open a desktop browser
clm edit --host 0.0.0.0 --port 9000 --no-browser
```

| Option | Default | Description |
|--------|---------|-------------|
| `--data-dir DIR` | `CLM_DATA_DIR` env or cwd | Course data directory containing `slides/` |
| `--host TEXT` | `127.0.0.1` | Bind host; `0.0.0.0` exposes on the LAN |
| `--port INTEGER` | `8080` | Port |
| `--no-browser` | off | Don't auto-open a desktop browser |

When you bind to `0.0.0.0`, the command prints the LAN URLs (e.g.
`http://192.168.1.42:8080`) **and a scannable ASCII QR code** — point your
phone's camera at the terminal to open the editor without typing the address.
The editor's landing page (`/`) also shows the QR as an inline SVG you can scan
from the desktop browser. The desktop URL (`http://localhost:8080`) works for
previewing from the same machine.

## Opening it on your phone

1. **Same network (home/office Wi-Fi):** run `clm edit --host 0.0.0.0`, then
   open the printed `http://<your-ip>:8080` URL in your phone's browser.
2. **Remote (anywhere):** install [Tailscale](https://tailscale.com/) on both
   machines, then open `http://<tailscale-hostname>:8080`. This keeps the
   editor private to your devices without exposing it to the internet.

> **Security:** the editor has no authentication and writes straight to your
> source files. Bind to `0.0.0.0` only on a network you trust, or use a
> Tailscale tunnel. Never expose it directly to the public internet.

## Editing decks

The landing page lists every module → topic → deck file found under
`slides/`. Tap a deck to open it.

Each cell is shown as a card with:

- a **header chip row** (cell kind, tags, language),
- a **body preview** (the cell's content),
- and action buttons: **Edit**, **↑** / **↓** (move), **Del** (delete).

### Editing a cell

Tap **Edit** to open an inline form with two fields:

- **Header** — the full percent-format header line (e.g.
  `# %% [markdown] lang="de" tags=["slide"]`).
- **Body** — the cell's content as plain text.

A row of **preset chips** fills common headers in one tap (DE/EN slide,
voiceover, notes, code), so you rarely need to type a header by hand.

### Adding a cell

**+ Add cell below** (or *at top*) reveals an insert form with the same
header/body fields and presets.

### Reordering and deleting

Use **↑** / **↓** to move a cell, and **Del** to remove it (with a confirm
prompt). Deleting the last cell still preserves the file's trailing newline.

## How edits are stored

Every save writes directly to the source file on disk using CLM's lossless
cell primitives. **Untouched cells are preserved byte-for-byte** — a save
that edits one cell changes only that cell's bytes, so diffs stay small and
reviewable.

- **Concurrent edits** (two phones, or phone + desktop): each save re-reads
  the file from disk before applying, so the last write wins and no edit acts
  on stale cell positions.
- **Safety net:** use git. Stage and review changes before committing, just
  as you would after editing in VS Code.

## What's not included

The editor focuses on per-cell editing of the file in front of you. For
bilingual sync assistance, use `clm slides sync` (or the MCP tools); for
rendered-slide previews, run `clm build`.
