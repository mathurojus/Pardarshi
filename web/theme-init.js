// Runs before first paint (loaded synchronously in <head>) so a saved theme never flashes the wrong colours.
// A separate file, not an inline script, because the Content-Security-Policy forbids inline scripts.
(function () {
  try {
    var t = localStorage.getItem("pardarshi-theme");
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
  } catch (e) { /* storage may be blocked: fall back to the system theme */ }
})();
