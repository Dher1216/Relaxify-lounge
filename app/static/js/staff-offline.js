// Registers the service worker and gives the sale form a way to queue a
// submission locally when there's no connection (window.queueOfflineSale).

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/static/service-worker.js").catch(function (err) {
    console.warn("Service worker registration failed:", err);
  });
}

function openQueueDB() {
  return new Promise(function (resolve, reject) {
    const req = indexedDB.open("relaxify-offline-queue", 1);
    req.onupgradeneeded = function () {
      req.result.createObjectStore("sales", { keyPath: "id", autoIncrement: true });
    };
    req.onsuccess = function () { resolve(req.result); };
    req.onerror = function () { reject(req.error); };
  });
}

function addQueued(db, payload) {
  return new Promise(function (resolve, reject) {
    const tx = db.transaction("sales", "readwrite");
    tx.objectStore("sales").add({ payload: payload, queuedAt: new Date().toISOString() });
    tx.oncomplete = function () { resolve(); };
    tx.onerror = function () { reject(tx.error); };
  });
}

function countQueued(db) {
  return new Promise(function (resolve, reject) {
    const tx = db.transaction("sales", "readonly");
    const req = tx.objectStore("sales").count();
    req.onsuccess = function () { resolve(req.result); };
    req.onerror = function () { reject(req.error); };
  });
}

// Called from the sale form's submit handler when navigator.onLine is false.
window.queueOfflineSale = async function (formData) {
  const payload = {};
  formData.forEach(function (value, key) { payload[key] = value; });

  const db = await openQueueDB();
  await addQueued(db, payload);
  const pending = await countQueued(db);
  updatePendingBanner(pending);

  // Ask for a Background Sync as soon as we're back online (Android/Chrome).
  if ("serviceWorker" in navigator && "SyncManager" in window) {
    try {
      const reg = await navigator.serviceWorker.ready;
      await reg.sync.register("sync-offline-sales");
    } catch (e) {
      // Background Sync not available - the 'online' event listener below is the fallback.
    }
  }
};

function updatePendingBanner(count) {
  const el = document.getElementById("pendingBanner");
  if (!el) return;
  if (count > 0) {
    el.style.display = "block";
    el.innerText = count + " sale" + (count === 1 ? "" : "s") + " saved on this device, waiting to send.";
  } else {
    el.style.display = "none";
  }
}

// Fallback for browsers/situations where Background Sync doesn't fire on its
// own: the moment THIS page notices it's back online, nudge the service worker
// to try syncing right away.
window.addEventListener("online", function () {
  if (navigator.serviceWorker && navigator.serviceWorker.controller) {
    navigator.serviceWorker.controller.postMessage({ type: "TRY_SYNC" });
  }
});

// The service worker tells us when a queued sale successfully syncs, so we can
// update the on-screen "pending" count and today's-sales count without staff
// needing to do anything.
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.addEventListener("message", async function (event) {
    if (event.data && event.data.type === "SALE_SYNCED") {
      const db = await openQueueDB();
      const pending = await countQueued(db);
      updatePendingBanner(pending);
      const countEl = document.getElementById("todaysSalesCount");
      if (countEl) countEl.innerText = (parseInt(countEl.innerText, 10) || 0) + 1;
    }
  });
}

// Show the pending count on page load too, in case there were queued sales from
// a previous offline session that haven't synced yet.
(async function () {
  try {
    const db = await openQueueDB();
    const pending = await countQueued(db);
    updatePendingBanner(pending);
  } catch (e) { /* IndexedDB not available - offline queueing simply won't work on this browser */ }
})();
