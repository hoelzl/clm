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

// Bump SHELL_CACHE whenever a shell asset changes. Changing this file's bytes
// is what makes the browser re-run `install`, and the new cache name is what
// makes `activate` drop the old contents — a shell asset edited without a bump
// is served from cache forever. That is not hypothetical: app.js changed three
// times under `-v1`, so the S7 XSS fix would have reached no installed PWA at
// all. The stale-while-revalidate handler below is the belt to this braces —
// it means a missed bump costs one stale load rather than permanent staleness.
const SHELL_CACHE = "clm-studio-shell-v2";
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

  // App shell: stale-while-revalidate. Answer from cache (instant, works
  // offline) but always refetch in the background and store the result, so an
  // updated app.js is picked up on the next load even if SHELL_CACHE was not
  // bumped. Pure cache-first made a shell asset permanently immutable, which
  // is how a security fix ends up undeliverable.
  if (url.pathname.startsWith("/studio/")) {
    event.respondWith(
      caches.match(req).then((hit) => {
        const fresh = fetch(req)
          .then((resp) => {
            if (resp && resp.ok) {
              const copy = resp.clone();
              caches.open(SHELL_CACHE).then((cache) => cache.put(req, copy));
            }
            return resp;
          })
          .catch(() => hit); // offline: the cached copy is the answer
        return hit || fresh;
      })
    );
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
