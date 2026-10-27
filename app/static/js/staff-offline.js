// Staff "Record a Sale" engine: SAVE FIRST, SEND IN THE BACKGROUND.
//
// Every sale is written to this phone the moment Submit is tapped, so the screen
// can say "Saved" at once - with full signal, weak signal or none. The sale is then
// sent to the server in the background, retried automatically until it gets
// through, and its row in the Deluxe/King list changes from "sending" to the
// receipt number. Staff never see a hanging page or a connection error, so there is
// never a reason to enter the same sale twice. The same unique token travels with
// the sale on every retry, so the server can never record it twice either.
//
// (The service worker also sends waiting sales when the app is closed - see sw.js.)

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
function idbAll(db) {
  return new Promise(function (resolve, reject) {
    const req = db.transaction("sales", "readonly").objectStore("sales").getAll();
    req.onsuccess = function () { resolve(req.result); };
    req.onerror = function () { reject(req.error); };
  });
}
function idbAdd(db, payload) {
  return new Promise(function (resolve, reject) {
    const tx = db.transaction("sales", "readwrite");
    tx.objectStore("sales").add({ payload: payload, queuedAt: new Date().toISOString() });
    tx.oncomplete = function () { resolve(); };
    tx.onerror = function () { reject(tx.error); };
  });
}
function idbRemove(db, id) {
  return new Promise(function (resolve, reject) {
    const tx = db.transaction("sales", "readwrite");
    tx.objectStore("sales").delete(id);
    tx.oncomplete = function () { resolve(); };
    tx.onerror = function () { reject(tx.error); };
  });
}

// ---------------- Rows in the Deluxe / King lists ----------------
function tbodyFor(chair) {
  return document.getElementById(chair === "King" ? "kingRows" : "deluxeRows");
}
function manilaTime(iso) {
  try {
    return new Date(iso).toLocaleTimeString("en-US", { timeZone: "Asia/Manila", hour: "2-digit", minute: "2-digit" });
  } catch (e) { return ""; }
}
function describe(p) {
  return p.chair_type + " " + p.duration_minutes + "min" + (p.eye_massager === "1" ? " + Eye Massager" : "");
}
function rowFor(token) {
  return document.querySelector('tr[data-token="' + token + '"]');
}
function setRowStatus(tr, kind, text) {
  const s = tr.querySelector(".sale-status");
  if (!s) return;
  s.textContent = text;
  s.style.color = kind === "sent" ? "#7fd18b" : kind === "error" ? "#ff9b8a" : "var(--gold-bright, #e3c06a)";
}
function addPendingRow(payload) {
  const body = tbodyFor(payload.chair_type);
  if (!body || rowFor(payload.client_token)) return;
  const empty = body.querySelector(".empty-row");
  if (empty) empty.remove();
  const tr = document.createElement("tr");
  tr.dataset.token = payload.client_token;
  const t = document.createElement("td"); t.textContent = manilaTime(payload.occurred_at);
  const d = document.createElement("td"); d.textContent = describe(payload) + " ";
  const s = document.createElement("span"); s.className = "sale-status"; d.appendChild(s);
  tr.appendChild(t); tr.appendChild(d);
  body.insertBefore(tr, body.firstChild);
  setRowStatus(tr, "pending", "⏳ sending…");
}
function markSent(token, ref) {
  const tr = rowFor(token);
  if (tr) setRowStatus(tr, "sent", "✓ " + (ref || "saved"));
}

function updatePendingBanner(count, authProblem) {
  const el = document.getElementById("pendingBanner");
  if (!el) return;
  if (authProblem) {
    el.style.display = "block";
    el.innerHTML = "Your sales are safe on this phone, but you need to <a href=\"/login\" style=\"color:inherit;text-decoration:underline\">log in again</a> to send them.";
  } else if (count > 0) {
    el.style.display = "block";
    el.innerText = count + " sale" + (count === 1 ? "" : "s") + " saved on this phone, sending automatically.";
  } else {
    el.style.display = "none";
  }
}
function updateConnectionBanner() {
  const el = document.getElementById("offlineBanner");
  if (el) el.style.display = navigator.onLine ? "none" : "block";
}

// ---------------- Save locally (called when Submit is tapped) ----------------
// Resolves once the sale is safely stored on the phone. Rejects if the browser
// can't store it (then the page falls back to sending directly).
window.saveSaleLocally = async function (formData) {
  const payload = {};
  formData.forEach(function (value, key) { payload[key] = value; });
  const db = await openQueueDB();
  await idbAdd(db, payload);
  addPendingRow(payload);
  const countEl = document.getElementById("todaysSalesCount");
  if (countEl) countEl.innerText = (parseInt(countEl.innerText, 10) || 0) + 1;
  updatePendingBanner((await idbAll(db)).length);

  if ("serviceWorker" in navigator && "SyncManager" in window) {
    navigator.serviceWorker.ready.then(function (reg) { return reg.sync.register("sync-offline-sales"); }).catch(function () {});
  }
  window.syncNow();
  return payload;
};

// Direct send, only used if the phone's storage isn't available. Resolves to the
// server's JSON answer; rejects on a network problem.
window.sendDirect = function (payload) {
  return fetch("/staff/sale/api", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload), credentials: "same-origin",
  }).then(function (r) { return r.json(); });
};

// ---------------- Background sending ----------------
let syncing = false;
window.syncNow = async function () {
  if (syncing) return;
  syncing = true;
  let authProblem = false;
  let db;
  try {
    db = await openQueueDB();
    const items = await idbAll(db);
    for (const item of items) {
      const ctl = new AbortController();
      const timer = setTimeout(function () { ctl.abort(); }, 20000); // weak signal: don't wait forever
      try {
        const res = await fetch("/staff/sale/api", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(item.payload), credentials: "same-origin", signal: ctl.signal,
        });
        clearTimeout(timer);
        if (res.status === 401 || res.redirected) { authProblem = true; break; }
        const result = await res.json();
        if (result.ok) {
          await idbRemove(db, item.id);
          markSent(item.payload.client_token, result.reference_number);
        } else {
          const tr = rowFor(item.payload.client_token);
          if (tr) setRowStatus(tr, "error", "⚠ not accepted yet - will retry");
        }
      } catch (err) {
        clearTimeout(timer);
        break; // no signal / timed out: leave everything queued, try again shortly
      }
    }
  } catch (e) {
    // IndexedDB unavailable - nothing queued to send.
  } finally {
    syncing = false;
    try { updatePendingBanner(db ? (await idbAll(db)).length : 0, authProblem); } catch (e) {}
  }
};

// ---------------- Startup ----------------
window.addEventListener("online", function () { updateConnectionBanner(); window.syncNow(); });
window.addEventListener("offline", updateConnectionBanner);
document.addEventListener("visibilitychange", function () { if (!document.hidden) window.syncNow(); });
setInterval(window.syncNow, 10000);

// The service worker tells us when it sent a sale while this page was open.
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.addEventListener("message", function (event) {
    if (event.data && event.data.type === "SALE_SYNCED") {
      markSent(event.data.clientToken, event.data.referenceNumber);
      window.syncNow(); // refresh the waiting-count banner
    }
  });
}

document.addEventListener("DOMContentLoaded", async function () {
  updateConnectionBanner();
  try {
    // Sales still waiting from earlier: show them in the lists again. Skip any that
    // the server already has (listed with the same token) - they just need clearing.
    const db = await openQueueDB();
    const items = await idbAll(db);
    const serverTokens = {};
    document.querySelectorAll("tr[data-token]").forEach(function (tr) { if (tr.dataset.token) serverTokens[tr.dataset.token] = true; });
    let stillWaiting = 0;
    for (const it of items) {
      if (serverTokens[it.payload.client_token]) { await idbRemove(db, it.id); continue; }
      addPendingRow(it.payload);
      stillWaiting++;
    }
    const countEl = document.getElementById("todaysSalesCount");
    if (countEl && stillWaiting) countEl.innerText = (parseInt(countEl.innerText, 10) || 0) + stillWaiting;
    updatePendingBanner(stillWaiting);
    if (stillWaiting) window.syncNow();
  } catch (e) { /* IndexedDB not available: sales are sent directly instead */ }
});

// Compatibility for a staff page saved on the phone by an older version of the app.
window.queueOfflineSale = function (formData) { return window.saveSaleLocally(formData); };
window.recentQueuedDuplicate = function () { return Promise.resolve(false); };
