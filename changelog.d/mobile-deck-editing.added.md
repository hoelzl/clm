- **Mobile deck editor (`clm edit`).** New LAN-served, offline web editor for
  percent-format deck files (`.py`, `.cpp`, `.cs`, `.java`, `.ts`). Run `clm edit`
  on your desktop, open the printed URL on your phone over the same Wi-Fi, and
  edit slides cell-by-cell (read / edit / add / delete / reorder) with
  header-preset chips so you rarely type a cell header by hand. Every save writes
  straight to the real file — untouched cells are preserved byte-for-byte, and git
  is your safety net. Backed by the lossless `raw_cells` / `DeckFile` primitives;
  ships as the `[edit]` extra (HTMX + Jinja2 + python-multipart + segno).
- **QR code for mobile pairing.** `clm edit` prints a scannable ASCII QR code in
  the desktop terminal and shows one on the editor's landing page, so you can open
  the editor on your phone by scanning instead of typing a LAN URL. Generated
  fully offline (server-side SVG via segno; no external API).
