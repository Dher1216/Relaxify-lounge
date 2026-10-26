// Show / hide the password on the sign-in form.
(function () {
  var input = document.getElementById("loginPassword");
  var btn = document.getElementById("pwToggle");
  if (!input || !btn) return;
  btn.addEventListener("click", function () {
    var show = input.type === "password";
    input.type = show ? "text" : "password";
    btn.textContent = show ? "Hide" : "Show";
    btn.setAttribute("aria-pressed", show ? "true" : "false");
    btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
    input.focus();
  });
})();
