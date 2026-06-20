/*
 * Mobile Deck Studio — P0/P1 lightweight frontend.
 *
 * Per design §9.4 this is intentionally a vanilla-JS surface (no build, no
 * CodeMirror/Preact yet); the no-build Preact + CodeMirror PWA is deferred to
 * P2+ where in-cell editing ergonomics start to pay off. It exercises the full
 * backend contract: browse (tree/recents/orphans), search, open deck, and cell
 * editing with optimistic-concurrency guards + the disk-change banner.
 */
"use strict";

const TOKEN_KEY = "clm_studio_token";

// --- token: from ?token= (QR deep link) then localStorage ---------------------
function resolveToken() {
  const url = new URL(window.location.href);
  const fromQuery = url.searchParams.get("token");
  if (fromQuery) {
    localStorage.setItem(TOKEN_KEY, fromQuery);
    url.searchParams.delete("token");
    window.history.replaceState({}, "", url.pathname + url.hash);
    return fromQuery;
  }
  return localStorage.getItem(TOKEN_KEY) || "";
}

let TOKEN = resolveToken();

// --- API ----------------------------------------------------------------------
async function api(path, opts = {}) {
  const headers = Object.assign({ Authorization: "Bearer " + TOKEN }, opts.headers || {});
  if (opts.body) headers["Content-Type"] = "application/json";
  const res = await fetch("/api/studio" + path, Object.assign({}, opts, { headers }));
  if (!res.ok) {
    let detail;
    try { detail = (await res.json()).detail; } catch { detail = res.statusText; }
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = res.status;
    err.detail = detail;
    throw err;
  }
  return res.json();
}

// --- tiny markdown renderer (tier-1 client preview) ---------------------------
function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function renderMarkdown(src) {
  const lines = src.split("\n");
  const out = [];
  let inCode = false, inList = false;
  const flushList = () => { if (inList) { out.push("</ul>"); inList = false; } };
  for (const line of lines) {
    if (/^```/.test(line)) {
      if (!inCode) { flushList(); out.push("<pre><code>"); inCode = true; }
      else { out.push("</code></pre>"); inCode = false; }
      continue;
    }
    if (inCode) { out.push(esc(line)); continue; }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) { flushList(); out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); continue; }
    const li = line.match(/^\s*[-*]\s+(.*)$/);
    if (li) { if (!inList) { out.push("<ul>"); inList = true; } out.push(`<li>${inline(li[1])}</li>`); continue; }
    if (line.trim() === "") { flushList(); continue; }
    flushList();
    out.push(`<p>${inline(line)}</p>`);
  }
  flushList();
  if (inCode) out.push("</code></pre>");
  return out.join("\n");
}
function inline(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

// --- UI helpers ---------------------------------------------------------------
const appEl = document.getElementById("app");
const titleEl = document.getElementById("title");
const backEl = document.getElementById("back");
const toastEl = document.getElementById("toast");
let toastTimer = null;
function toast(msg) {
  toastEl.textContent = msg;
  toastEl.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove("show"), 2200);
}
function el(html) { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstChild; }

// --- state --------------------------------------------------------------------
let currentDeck = null; // { deck_id, deck_version, cells: [...] }
let diskChanged = false;
let reorderMode = false;

// Shared write-error handling for every mutating call.
function handleWriteError(e, label) {
  if (e.status === 409) {
    diskChanged = true;
    toast("Changed elsewhere — reload.");
    renderDeck();
  } else if (e.status === 423) {
    const reason = (e.detail && e.detail.reason) || "Language locked — sync or discard first.";
    toast(reason);
    if (currentDeck) openDeck(currentDeck.deck_id); // refresh to surface the lock banner
  } else {
    toast(label + " failed: " + e.message);
  }
}

// --- views --------------------------------------------------------------------
async function showHome() {
  currentDeck = null; diskChanged = false;
  backEl.style.display = "none";
  titleEl.textContent = "Mobile Deck Studio";
  appEl.innerHTML = '<p class="muted">Loading decks&hellip;</p>';
  if (!TOKEN) { appEl.innerHTML = '<div class="banner err">No pairing token. Re-scan the QR code from the desktop.</div>'; return; }

  let tree;
  try { tree = await api("/decks"); }
  catch (e) { appEl.innerHTML = `<div class="banner err">Could not load decks: ${esc(e.message)}</div>`; return; }

  appEl.innerHTML = "";
  const search = el(`<input type="search" placeholder="Search decks & cells…" autocomplete="off" />`);
  let searchTimer = null;
  search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    const q = search.value.trim();
    searchTimer = setTimeout(() => renderSearch(q, results), 250);
  });
  appEl.appendChild(search);
  const results = el(`<div></div>`);
  appEl.appendChild(results);

  if (tree.recents && tree.recents.length) {
    appEl.appendChild(el(`<div class="section-title">Recent</div>`));
    tree.recents.forEach((id) => appEl.appendChild(deckRow({ deck_id: id, filename: id.split("/").pop(), status: "present" })));
  }

  appEl.appendChild(el(`<div class="section-title">In spec</div>`));
  const present = tree.decks.filter((d) => d.status === "present");
  const missing = tree.decks.filter((d) => d.status === "missing");
  if (!present.length) appEl.appendChild(el(`<p class="muted">No decks resolved.</p>`));
  present.forEach((d) => appEl.appendChild(deckRow(d)));
  missing.forEach((d) => appEl.appendChild(deckRow(d)));

  if (tree.orphans && tree.orphans.length) {
    appEl.appendChild(el(`<div class="section-title">Not in spec</div>`));
    tree.orphans.forEach((d) => appEl.appendChild(deckRow(d)));
  }
}

async function renderSearch(q, container) {
  if (!q) { container.innerHTML = ""; return; }
  try {
    const res = await api("/search?q=" + encodeURIComponent(q));
    container.innerHTML = `<div class="section-title">Results for &ldquo;${esc(q)}&rdquo;</div>`;
    if (!res.hits.length) { container.appendChild(el(`<p class="muted">No matches.</p>`)); return; }
    res.hits.forEach((h) => {
      const title = h.title_en || h.title_de || h.topic_id;
      h.deck_ids.forEach((id) =>
        container.appendChild(deckRow({ deck_id: id, filename: id.split("/").pop(), topic_id: title, status: "present" })));
    });
  } catch (e) { container.innerHTML = `<div class="banner err">${esc(e.message)}</div>`; }
}

function deckRow(d) {
  const openable = d.status !== "missing" && d.deck_id;
  const node = el(`<div class="card deck-item row">
      <div class="spacer">
        <div><span class="chip ${d.status}">${d.status}</span>${esc(d.filename || d.deck_id)}</div>
        ${d.topic_id ? `<div class="muted" style="font-size:.8rem">${esc(d.topic_id)}</div>` : ""}
      </div>
    </div>`);
  if (openable) node.addEventListener("click", () => openDeck(d.deck_id));
  else node.style.opacity = ".6";
  return node;
}

async function openDeck(deckId) {
  appEl.innerHTML = '<p class="muted">Opening&hellip;</p>';
  reorderMode = false;
  try {
    currentDeck = await api("/deck?id=" + encodeURIComponent(deckId));
    diskChanged = false;
    renderDeck();
  } catch (e) {
    appEl.innerHTML = `<div class="banner err">Could not open deck: ${esc(e.message)}</div>`;
  }
}

// Language toggle + lock state (P3): DE ⚫ / EN 🔒, tap the twin to switch.
function languageBar(lock) {
  const bar = el(`<div class="lang-bar row"></div>`);
  bar.appendChild(el(`<span class="lang-chip active">${esc((lock.lang || "").toUpperCase())} ${lock.editable ? "✎" : "🔒"}</span>`));
  if (lock.twin_deck_id) {
    const other = el(`<button class="lang-chip ghost">${esc((lock.other_lang || "").toUpperCase())} →</button>`);
    other.addEventListener("click", () => openDeck(lock.twin_deck_id));
    bar.appendChild(other);
  }
  if (lock.other_stale) {
    bar.appendChild(el(`<span class="spacer"></span>`));
    bar.appendChild(el(`<span class="chip stale-chip">${esc((lock.other_lang || "").toUpperCase())} stale</span>`));
  }
  return bar;
}

function renderDeck() {
  const deck = currentDeck;
  const lock = deck.lock || { is_pair: false, editable: true };
  const locked = lock.is_pair && !lock.editable;
  backEl.style.display = "";
  titleEl.textContent = deck.deck_id.split("/").pop();
  appEl.innerHTML = "";

  if (lock.is_pair) appEl.appendChild(languageBar(lock));
  if (locked) {
    appEl.appendChild(el(`<div class="banner warn">🔒 ${esc(lock.locked_reason || "This language is locked.")}</div>`));
  } else if (lock.other_stale) {
    appEl.appendChild(el(`<div class="banner stale">${esc((lock.other_lang || "other").toUpperCase())} is stale — your edits aren't propagated yet. Sync from the desktop for now.</div>`));
  }

  if (diskChanged) {
    const b = el(`<div class="banner warn row">Changed on disk — reload to avoid conflicts.
      <span class="spacer"></span></div>`);
    const r = el(`<button>Reload</button>`);
    r.addEventListener("click", () => openDeck(deck.deck_id));
    b.appendChild(r);
    appEl.appendChild(b);
  }

  // Toolbar: reorder toggle + add-at-start (hidden when the language is locked).
  if (!locked) {
    const bar = el(`<div class="row toolbar"></div>`);
    const reorderBtn = el(`<button class="ghost">${reorderMode ? "✓ Reordering" : "⇅ Reorder"}</button>`);
    reorderBtn.addEventListener("click", () => { reorderMode = !reorderMode; renderDeck(); });
    bar.appendChild(reorderBtn);
    bar.appendChild(el(`<span class="spacer"></span>`));
    if (!reorderMode) {
      const addBtn = el(`<button>+ Add slide</button>`);
      addBtn.addEventListener("click", () => openInsertForm(null));
      bar.appendChild(addBtn);
    }
    appEl.appendChild(bar);
  }

  deck.cells.forEach((cell, i) => appEl.appendChild(cellCard(cell, i, locked)));
}

function cellCard(cell, idx, locked) {
  const canEdit = cell.editable && !locked;
  const langChip = cell.lang ? `<span class="chip">${cell.lang}</span>` : "";
  const tagChips = (cell.tags || []).map((t) => `<span class="chip">${esc(t)}</span>`).join("");
  const card = el(`<div class="cell ${canEdit && !reorderMode ? "editable" : ""}">
      <div class="cell-head">
        <span class="chip">${cell.cell_type}</span>${langChip}${tagChips}
        <span class="spacer"></span>
        <span class="cell-controls"></span>
      </div>
      <div class="cell-body"></div>
    </div>`);
  const body = card.querySelector(".cell-body");
  if (cell.cell_type === "markdown") body.innerHTML = renderMarkdown(cell.body);
  else body.innerHTML = `<pre><code>${esc(cell.body)}</code></pre>`;

  const controls = card.querySelector(".cell-controls");
  if (locked) {
    controls.appendChild(el(`<span class="muted">🔒 locked</span>`));
  } else if (reorderMode) {
    if (canEdit) {
      const up = el(`<button class="ghost icon" title="Move up">↑</button>`);
      const down = el(`<button class="ghost icon" title="Move down">↓</button>`);
      up.addEventListener("click", () => moveCell(cell, "up"));
      down.addEventListener("click", () => moveCell(cell, "down"));
      controls.appendChild(up); controls.appendChild(down);
    } else {
      controls.appendChild(el(`<span class="muted">read-only</span>`));
    }
  } else if (canEdit) {
    const ins = el(`<button class="ghost icon" title="Insert after">＋</button>`);
    const del = el(`<button class="ghost icon" title="Delete">🗑</button>`);
    ins.addEventListener("click", (e) => { e.stopPropagation(); openInsertForm(cell); });
    del.addEventListener("click", (e) => { e.stopPropagation(); deleteCell(cell); });
    controls.appendChild(el(`<span class="muted">tap to edit</span>`));
    controls.appendChild(ins); controls.appendChild(del);
    card.querySelector(".cell-head").addEventListener("click", () => editCell(cell, idx));
  } else {
    controls.appendChild(el(`<span class="muted">read-only</span>`));
  }
  return card;
}

async function moveCell(cell, direction) {
  if (diskChanged) { toast("Deck changed on disk — reload first."); return; }
  try {
    await api("/deck/move", {
      method: "POST",
      body: JSON.stringify({
        deck_id: currentDeck.deck_id,
        slide_id: cell.slide_id,
        role: cell.role,
        direction,
        expected_deck_version: currentDeck.deck_version,
      }),
    });
    await openDeck(currentDeck.deck_id); // reload: fresh order + guards
    reorderMode = true; renderDeck();    // openDeck reset the view; stay in reorder
  } catch (e) {
    if (e.status === 400) { toast("Already at the " + (direction === "up" ? "top" : "bottom") + "."); }
    else handleWriteError(e, "Move");
  }
}

async function deleteCell(cell) {
  if (diskChanged) { toast("Deck changed on disk — reload first."); return; }
  if (!window.confirm(`Delete this ${cell.role || "cell"} (${cell.slide_id || ""})?`)) return;
  try {
    await api("/deck/delete", {
      method: "POST",
      body: JSON.stringify({
        deck_id: currentDeck.deck_id,
        slide_id: cell.slide_id,
        role: cell.role,
        expected_deck_version: currentDeck.deck_version,
        expected_cell_hash: cell.content_hash,
      }),
    });
    toast("Deleted");
    await openDeck(currentDeck.deck_id);
  } catch (e) {
    handleWriteError(e, "Delete");
  }
}

// Insert a new cell after `anchor` (or at the deck start when anchor is null).
function openInsertForm(anchor) {
  if (diskChanged) { toast("Deck changed on disk — reload first."); return; }
  appEl.innerHTML = "";
  titleEl.textContent = anchor ? "Insert after " + (anchor.slide_id || anchor.role) : "Add slide";

  const typeSel = el(`<select><option value="markdown">markdown</option><option value="code">code</option></select>`);
  const roleInput = el(`<input type="text" value="slide" />`);
  // Markdown narrative roles are common; "code" is forced for code cells.
  typeSel.addEventListener("change", () => {
    if (typeSel.value === "code") { roleInput.value = "code"; roleInput.disabled = true; }
    else { roleInput.disabled = false; if (roleInput.value === "code") roleInput.value = "slide"; }
  });

  let shareWrap = null, shareChk = null;
  if (anchor && anchor.slide_id) {
    shareChk = el(`<input type="checkbox" />`);
    shareWrap = el(`<label class="row" style="gap:6px"></label>`);
    shareWrap.appendChild(shareChk);
    shareWrap.appendChild(el(`<span>Share id with anchor (e.g. notes for this slide)</span>`));
  }

  const ta = el(`<textarea placeholder="# Title&#10;#&#10;# Body (markdown lines start with &quot;# &quot;)"></textarea>`);

  appEl.appendChild(el(`<div class="muted" style="margin-bottom:6px">New cell ${anchor ? "after " + esc(anchor.slide_id || anchor.role || "") : "at deck start"}</div>`));
  const form = el(`<div class="insert-form"></div>`);
  form.appendChild(labeled("Type", typeSel));
  form.appendChild(labeled("Role / tag", roleInput));
  if (shareWrap) form.appendChild(shareWrap);
  appEl.appendChild(form);
  appEl.appendChild(ta);

  const actions = el(`<div class="edit-actions"></div>`);
  const save = el(`<button>Insert</button>`);
  const cancel = el(`<button class="ghost">Cancel</button>`);
  actions.appendChild(save); actions.appendChild(cancel);
  appEl.appendChild(actions);
  ta.focus();

  cancel.addEventListener("click", renderDeck);
  save.addEventListener("click", async () => {
    save.disabled = true; cancel.disabled = true;
    const payload = {
      deck_id: currentDeck.deck_id,
      cell_type: typeSel.value,
      role: roleInput.value.trim(),
      body: ta.value,
      after_slide_id: anchor ? anchor.slide_id : null,
      after_role: anchor ? anchor.role : null,
      expected_deck_version: currentDeck.deck_version,
    };
    if (shareChk && shareChk.checked) payload.slide_id = anchor.slide_id;
    try {
      await api("/deck/insert", { method: "POST", body: JSON.stringify(payload) });
      toast("Inserted");
      await openDeck(currentDeck.deck_id);
    } catch (e) {
      save.disabled = false; cancel.disabled = false;
      handleWriteError(e, "Insert");
    }
  });
}

function labeled(label, control) {
  const wrap = el(`<label class="field"></label>`);
  wrap.appendChild(el(`<span class="muted">${esc(label)}</span>`));
  wrap.appendChild(control);
  return wrap;
}

function editCell(cell, idx) {
  if (diskChanged) { toast("Deck changed on disk — reload first."); return; }
  appEl.innerHTML = "";
  titleEl.textContent = "Edit " + (cell.slide_id || cell.role);
  const ta = el(`<textarea></textarea>`);
  ta.value = cell.body;
  appEl.appendChild(el(`<div class="muted" style="margin-bottom:6px">${cell.cell_type} · ${esc(cell.slide_id || "")} · ${esc(cell.role || "")}</div>`));
  appEl.appendChild(ta);
  const actions = el(`<div class="edit-actions"></div>`);
  const save = el(`<button>Save</button>`);
  const cancel = el(`<button class="ghost">Cancel</button>`);
  actions.appendChild(save); actions.appendChild(cancel);
  appEl.appendChild(actions);
  ta.focus();

  cancel.addEventListener("click", renderDeck);
  save.addEventListener("click", async () => {
    save.disabled = true; cancel.disabled = true;
    try {
      const result = await api("/deck/edit-body", {
        method: "POST",
        body: JSON.stringify({
          deck_id: currentDeck.deck_id,
          slide_id: cell.slide_id,
          role: cell.role,
          new_body: ta.value,
          expected_deck_version: currentDeck.deck_version,
          expected_cell_hash: cell.content_hash,
        }),
      });
      // Update in-memory state so further edits use fresh guards.
      currentDeck.deck_version = result.deck_version;
      cell.body = ta.value;
      cell.content_hash = result.cell_hash;
      toast("Saved");
      renderDeck();
    } catch (e) {
      save.disabled = false; cancel.disabled = false;
      handleWriteError(e, "Save");
    }
  });
}

// --- WebSocket: disk-change notifications -------------------------------------
function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  let ws;
  try { ws = new WebSocket(`${proto}://${location.host}/ws`); }
  catch { return; }
  ws.addEventListener("open", () => ws.send(JSON.stringify({ action: "subscribe", channels: ["studio"] })));
  ws.addEventListener("message", (ev) => {
    let msg; try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === "deck-changed-on-disk" && currentDeck && msg.deck_id === currentDeck.deck_id) {
      diskChanged = true;
      renderDeck();
    }
  });
  ws.addEventListener("close", () => setTimeout(connectWs, 3000));
}

// --- wiring -------------------------------------------------------------------
backEl.addEventListener("click", showHome);
document.getElementById("refresh").addEventListener("click", () => (currentDeck ? openDeck(currentDeck.deck_id) : showHome()));
connectWs();
showHome();
