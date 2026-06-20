/*
 * Mobile Deck Studio service worker (P4) — installable PWA + read-only offline.
 *
 * Registered with root scope (served with `Service-Worker-Allowed: /`) so it can
 * cache both the `/studio/` app shell and `/api/studio/deck{,s}` reads. Strategy:
 *   - app shell  → cache-first  (instant load, works offline)
 *   - deck reads → network-first, fall back to cache when offline (read-only)
 *   - writes / everything else → passthrough (never cached; the optimistic
 *     concurrency guards stay authoritative)
 *
 * "Offline" here is the away-from-desk fallback (design §1): view the last decks
 * you opened when the desktop is briefly unreachable. Editing requires the
 * desktop — writes are never served from cache.
 */
"use strict";

const SHELL_CACHE = "clm-studio-shell-v1";
const API_CACHE = "clm-studio-api-v1";
const SHELL = [
  "/studio/",
  "/studio/index.html",
  "/studio/app.js",
  "/studio/manifest.json",
  "/studio/icon.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== SHELL_CACHE && k !== API_CACHE).map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return; // writes pass through, never cached
  const url = new URL(req.url);

  // App shell: cache-first.
  if (url.pathname.startsWith("/studio/")) {
    event.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
    return;
  }

  // Deck reads: network-first, cache fallback when offline.
  if (url.pathname === "/api/studio/deck" || url.pathname === "/api/studio/decks") {
    event.respondWith(
      fetch(req)
        .then((resp) => {
          const copy = resp.clone();
          caches.open(API_CACHE).then((cache) => cache.put(req, copy));
          return resp;
        })
        .catch(() => caches.match(req))
    );
    return;
  }
  // Everything else: default network handling.
});
