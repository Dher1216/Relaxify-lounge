// Launcher: this page is saved on the phone, so it opens instantly even while the
// server (Render free plan) is asleep. It wakes the server with a small data-free
// request, shows a friendly message if that takes a while, and then moves on to
// the real page. No private data is read, stored or shown here.
(function () {
  var msg = document.getElementById("launchMsg");
  var spinner = document.getElementById("launchSpinner");
  var retryBtn = document.getElementById("launchRetry");

  // Where the person was heading. Opened from the icon (/launch) -> let the server pick.
  var OK_PATHS = ["/", "/login", "/staff/sale"];
  var next = OK_PATHS.indexOf(location.pathname) !== -1 ? location.pathname + location.search : "/";

  var wakeTimer = null;
  var finished = false;

  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  function lsDel(k) { try { localStorage.removeItem(k); } catch (e) {} }

  function showWaking() {
    if (finished) return;
    msg.textContent = "Opening the app. The server is waking up. This can take up to a minute the first time. Please wait.";
  }

  function finish() {
    finished = true;
    if (wakeTimer) { clearTimeout(wakeTimer); wakeTimer = null; }
  }

  function goOffline() {
    finish();
    spinner.style.display = "none";
    // Staff phones can still record sales with no signal: the sale form is saved on the device.
    if (lsGet("relaxify_last_home") === "/staff/sale") {
      msg.textContent = "No connection. Opening the saved sale screen…";
      location.replace("/staff/sale");
      return;
    }
    msg.textContent = "No internet connection. Please connect and try again.";
    retryBtn.style.display = "inline-block";
  }

  function attempt() {
    finished = false;
    retryBtn.style.display = "none";
    spinner.style.display = "block";
    msg.textContent = "Opening…";
    if (wakeTimer) clearTimeout(wakeTimer);
    wakeTimer = setTimeout(showWaking, 4000); // only if the first request is still unanswered
    ping();
  }

  function ping() {
    fetch("/session-ping", { cache: "no-store", credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        finish(); // cancel the timer on success
        if (d.logged_in) lsSet("relaxify_last_home", d.home); else lsDel("relaxify_last_home");
        var target = next;
        if (next === "/") target = d.home;
        location.replace(target);
      })
      .catch(function () {
        if (navigator.onLine === false) { goOffline(); return; }
        // Online but no valid answer yet (server still waking): keep trying quietly.
        setTimeout(ping, 3000);
      });
  }

  retryBtn.addEventListener("click", attempt);
  window.addEventListener("online", function () { if (retryBtn.style.display !== "none") attempt(); });
  attempt();
})();
