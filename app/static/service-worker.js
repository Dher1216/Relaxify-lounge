// Service worker for the Relaxify Lounge staff sales screen.
// Two jobs: (1) let /staff/sale open even with zero signal, by serving the last
// successfully-loaded copy from cache; (2) wake up automatically when the device
// gets signal back and push any queued offline sales to the server.

const CACHE_NAME = "relaxify-staff-v1";
const OFFLINE_URLS = ["/staff/sale", "/static/css/style.css", "/static/img/logo.png"];

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(OFFLINE_URLS).catch(() => {
      // Best-effort - if a resource fails to pre-cache (e.g. first install with no
      // network yet), the network-first handler below will still cache it on next
      // successful visit.
    }))
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

// Network-first, falling back to cache: always try to get the freshest page
// (current rates, current "today's sales" list) when online, but if the network
// request fails entirely (no signal), serve whatever was cached last time.
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.pathname.startsWith("/staff/sale/api")) {
    return; // never intercept the sync POST endpoint or non-GET requests
  }

  if (url.pathname === "/staff/sale" || OFFLINE_URLS.includes(url.pathname)) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          return response;
        })
        .catch(() => caches.match(event.request))
    );
  }
});

// Triggered by the browser automatically once connectivity returns, even if the
// app itself isn't open - this is what makes sync happen "by itself" on Android.
self.addEventListener("sync", (event) => {
  if (event.tag === "sync-offline-sales") {
    event.waitUntil(syncQueuedSales());
  }
});

// Fallback path for browsers without Background Sync support (or if registration
// failed) - the page itself asks the service worker to sync whenever it notices
// it's back online (see staff-offline.js).
self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "TRY_SYNC") {
    event.waitUntil ? event.waitUntil(syncQueuedSales()) : syncQueuedSales();
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
      // If not ok, leave it queued - it'll retry on the next sync event.
    } catch (err) {
      // Still offline or the server is unreachable - leave queued, try again later.
      break;
    }
  }
}

function notifyClients(message) {
  self.clients.matchAll().then((clients) => {
    clients.forEach((client) => client.postMessage(message));
  });
}

// --- Minimal IndexedDB helpers (kept here too, so the service worker can sync
// even if the page that queued the sale isn't open anymore) ---
function openQueueDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open("relaxify-offline-queue", 1);
    req.onupgradeneeded = () => {
      req.result.createObjectStore("sales", { keyPath: "id", autoIncrement: true });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function getAllQueued(db) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction("sales", "readonly");
    const req = tx.objectStore("sales").getAll();
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
