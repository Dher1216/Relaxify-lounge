// Runs on every page: registers the service worker, offers "Install" on phones,
// and clears device-saved private data when someone signs out.
(function () {
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  function lsDel(k) { try { localStorage.removeItem(k); } catch (e) {} }

  // ---- Service worker ----
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function (err) {
        console.warn("Service worker registration failed:", err);
      });
    });
  }

  // ---- Clear private data on sign-out ----
  function clearPrivate() {
    lsDel("relaxify_last_home");
    if (navigator.serviceWorker && navigator.serviceWorker.controller) {
      navigator.serviceWorker.controller.postMessage({ type: "CLEAR_PRIVATE" });
    }
  }
  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (f && f.getAttribute && f.getAttribute("action") === "/logout") clearPrivate();
  }, true);
  // Landing on the sign-in page means nobody is signed in (also covers expired sessions).
  if (location.pathname === "/login") clearPrivate();

  // ---- Install banner (phones only, not when already installed) ----
  var ua = navigator.userAgent || "";
  var isIOS = /iPhone|iPad|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  var isAndroid = /Android/.test(ua);
  var isStandalone = (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) || window.navigator.standalone === true;
  var isSafariIOS = isIOS && !/CriOS|FxiOS|EdgiOS|OPiOS/.test(ua);
  var DISMISS_KEY = "relaxify_install_dismissed";

  if (!(isIOS || isAndroid) || isStandalone || lsGet(DISMISS_KEY) === "1") return;

  var banner = null;
  var deferredPrompt = null;

  function hideBanner() { if (banner) { banner.remove(); banner = null; } }

  function buildBanner(text, withInstall) {
    if (banner || !document.body) return;
    banner = document.createElement("div");
    banner.className = "install-banner no-print";
    var img = document.createElement("img");
    img.src = "/static/img/icon-192.png"; img.alt = "";
    var box = document.createElement("div");
    box.className = "ib-text";
    var strong = document.createElement("strong");
    strong.textContent = "Install Relaxify Lounge";
    var span = document.createElement("span");
    span.textContent = text;
    box.appendChild(strong); box.appendChild(span);
    banner.appendChild(img); banner.appendChild(box);
    if (withInstall) {
      var inst = document.createElement("button");
      inst.type = "button"; inst.textContent = "Install";
      inst.addEventListener("click", function () {
        if (!deferredPrompt) return;
        deferredPrompt.prompt();
        deferredPrompt.userChoice.then(function () { deferredPrompt = null; hideBanner(); }, hideBanner);
      });
      banner.appendChild(inst);
    }
    var no = document.createElement("button");
    no.type = "button"; no.className = "btn-secondary"; no.textContent = "Not now";
    no.addEventListener("click", function () { lsSet(DISMISS_KEY, "1"); hideBanner(); });
    banner.appendChild(no);
    document.body.appendChild(banner);
  }

  window.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault();
    deferredPrompt = e;
    buildBanner("Add it to your home screen for quick access.", true);
  });
  window.addEventListener("appinstalled", hideBanner);

  if (isSafariIOS) {
    setTimeout(function () {
      buildBanner("In Safari, tap the Share button, then Add to Home Screen", false);
    }, 2500);
  }
})();
