// Relaxify Lounge service worker. Served from /sw.js (and, for devices that
// registered the older URL, from /service-worker.js) by app/main.py, which
// replaces __BUILD__ below with a fingerprint of the shell files. Every
// deploy that changes a shell file therefore gets a NEW cache name
// automatically, and the old cache is deleted on activate.
//
// What is saved on the device:
//   * the launcher page + CSS/JS/images (the "app shell") - public, no figures
//   * the staff "Record a Sale" page (needed to record sales with no signal)
// What is NEVER saved: reports, dashboard, transactions, ledgers, sign-in,
// /session-ping, any non-GET request, or any other website's requests.

const BUILD = "__BUILD__";
const SHELL_CACHE = "app-shell-" + BUILD;
const STAFF_CACHE = "staff-page-v1";

const SHELL_FILES = [
  "/launch",
  "/manifest.webmanifest",
  "/static/css/style.css",
  "/static/css/pwa.css",
  "/static/js/pwa.js",
  "/static/js/launch.js",
  "/static/js/staff-offline.js",
  "/static/img/logo.png",
  "/static/img/background.png",
  "/static/img/icon-192.png",
  "/static/img/icon-512.png",
  "/static/img/apple-touch-icon.png",
];

// Pages that, when opened straight from the home-screen icon / bookmark / address
// bar, are answered instantly with the launcher (which then wakes the server and
// moves on to the real page). Clicks INSIDE the app are never intercepted: a
// navigation started from inside the app always carries a referrer, while one
// started from the icon / bookmark / typed address has none.
const LAUNCH_PATHS = ["/", "/login", "/staff/sale"];

// Anything matching these always goes to the network untouched.
function isColdOpen(req) {
  return req.mode === "navigate" && !req.referrer;
}

const NEVER_INTERCEPT = ["/api/", "/graphql", "/staff/sale/api", "/session-ping", "/login", "/logout"];

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) =>
      // One file failing must not stop the rest from being saved.
      Promise.all(SHELL_FILES.map((u) => cache.add(u).catch(() => null)))
    )
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL_CACHE && k !== STAFF_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function staleWhileRevalidate(request, cacheKey) {
  return caches.open(SHELL_CACHE).then((cache) =>
    cache.match(cacheKey || request).then((cached) => {
      const refresh = fetch(request)
        .then((res) => {
          if (res && res.ok && !res.redirected) cache.put(cacheKey || request, res.clone());
          return res;
        })
        .catch(() => null);
      if (cached) {
        refresh.catch(() => null); // background refresh; ignore failures
        return cached;
      }
      return refresh.then((res) => res || Response.error());
    })
  );
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  const path = url.pathname;

  // Never touch data / sign-in / session routes (staff sync POST is non-GET anyway).
  if (NEVER_INTERCEPT.some((p) => (p.endsWith("/") ? path.startsWith(p) : path === p)) && !(isColdOpen(req) && LAUNCH_PATHS.includes(path))) {
    return;
  }

  if (req.mode === "navigate") {
    if (path === "/launch") {
      event.respondWith(staleWhileRevalidate(req));
      return;
    }
    // Opened from the home screen / bookmark / typed address: show the launcher at
    // once instead of waiting on a sleeping server.
    if (LAUNCH_PATHS.includes(path) && isColdOpen(req)) {
      event.respondWith(
        caches.open(SHELL_CACHE).then((cache) => cache.match("/launch")).then((cached) => cached || fetch(req))
      );
      return;
    }
    if (path === "/staff/sale") {
      // Staff sale form: network first, saved copy only if there is no signal.
      event.respondWith(
        fetch(req)
          .then((res) => {
            if (res.ok && !res.redirected) {
              const copy = res.clone();
              caches.open(STAFF_CACHE).then((c) => c.put("/staff/sale", copy));
            }
            return res;
          })
          .catch(() => caches.open(STAFF_CACHE).then((c) => c.match("/staff/sale")).then((r) => r || Response.error()))
      );
    }
    return;
  }

  // Static files and the manifest: answer from the saved copy, refresh in background.
  if (path.startsWith("/static/") || path === "/manifest.webmanifest") {
    event.respondWith(staleWhileRevalidate(req));
  }
});

// ---- Offline sales queue (unchanged behaviour) ----
self.addEventListener("sync", (event) => {
  if (event.tag === "sync-offline-sales") event.waitUntil(syncQueuedSales());
});

self.addEventListener("message", (event) => {
  const data = event.data || {};
  if (data.type === "TRY_SYNC") {
    event.waitUntil(syncQueuedSales());
  } else if (data.type === "CLEAR_PRIVATE") {
    // Signed out: drop the saved staff page (it contains that day's sales list).
    event.waitUntil(caches.delete(STAFF_CACHE));
  }
});

async function syncQueuedSales() {
  const db = await openQueueDB();
  const items = await getAllQueued(db);
  for (const item of items) {
    try {
      const res = await fetch("/staff/sale/api", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(item.payload),
      });
      const result = await res.json();
      if (result.ok) {
        await removeQueued(db, item.id);
        notifyClients({ type: "SALE_SYNCED", clientToken: item.payload.client_token, referenceNumber: result.reference_number });
      }
    } catch (err) {
      break; // still offline / server unreachable - leave queued
    }
  }
}

function notifyClients(message) {
  self.clients.matchAll().then((clients) => clients.forEach((c) => c.postMessage(message)));
}

function openQueueDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open("relaxify-offline-queue", 1);
    req.onupgradeneeded = () => { req.result.createObjectStore("sales", { keyPath: "id", autoIncrement: true }); };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}
function getAllQueued(db) {
  return new Promise((resolve, reject) => {
    const req = db.transaction("sales", "readonly").objectStore("sales").getAll();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}
function removeQueued(db, id) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction("sales", "readwrite");
    tx.objectStore("sales").delete(id);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}
