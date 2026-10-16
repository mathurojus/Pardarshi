"use strict";
/* Onboarding page: create account / sign in / demo, then a 2-step preference wizard. */

const COUNTRY_NAMES = { IN: "India", US: "United States", GB: "United Kingdom", CA: "Canada", DE: "Germany", FR: "France", JP: "Japan", AU: "Australia", SG: "Singapore", AE: "United Arab Emirates", OTHER: "Somewhere else" };
const USE_CASES = { shopping: "Online shopping", travel: "Travel and flights", tickets: "Event tickets", subscriptions: "Subscriptions", software: "Software and SaaS", food: "Food delivery" };
const EMAIL_RE = /^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$/;

/* ---------- small helpers ---------- */
const showErr = (id, msg) => { const el = $(id); el.textContent = msg || ""; el.classList.toggle("show", !!msg); };
const fieldErr = (group, msg) => {
  const g = $("g-" + group);
  g.classList.toggle("bad", !!msg);
  $("e-" + group).textContent = msg || "";
  g.querySelector("input").setAttribute("aria-invalid", msg ? "true" : "false");
};
const busy = (btn, on, text) => { btn.disabled = on; if (on) { btn.dataset.t = btn.textContent; btn.textContent = text; } else if (btn.dataset.t) btn.textContent = btn.dataset.t; };
const firstName = (u) => u.name.split(" ")[0];

/* ---------- theme button + preview animation ---------- */
PD.paintThemeButton();
$("theme").onclick = () => PD.setTheme(PD.currentTheme() === "dark" ? "light" : "dark");
requestAnimationFrame(() => setTimeout(() => document.querySelectorAll(".pv-bar span").forEach((s) => (s.style.width = s.dataset.w + "%")), 250));

/* ---------- tabs ---------- */
function setTab(which) {
  $(which === "in" ? "tab-in" : "tab-up").checked = true;
  $("formUp").hidden = which === "in";
  $("formIn").hidden = which !== "in";
}
document.querySelectorAll('input[name="tab"]').forEach((r) => (r.onchange = () => setTab(r.value)));
$("topSignin").onclick = () => { setTab("in"); $("auth").scrollIntoView({ behavior: "smooth", block: "center" }); setTimeout(() => $("si-email").focus(), 350); };
if (location.hash === "#signin") setTab("in");

/* ---------- show / hide password ---------- */
document.querySelectorAll("[data-toggle]").forEach((b) => (b.onclick = () => {
  const inp = $(b.dataset.toggle), show = inp.type === "password";
  inp.type = show ? "text" : "password";
  b.textContent = show ? "Hide" : "Show";
  b.setAttribute("aria-label", show ? "Hide password" : "Show password");
}));

/* ---------- password strength (mirrors the server's rules, for feedback only) ---------- */
function strength(pw) {
  if (!pw) return 0;
  let s = 0;
  if (pw.length >= 10) s++;
  if (pw.length >= 14) s++;
  if (/[A-Za-z]/.test(pw) && /[0-9]/.test(pw)) s++;
  if (/[^A-Za-z0-9]/.test(pw) || pw.length >= 18) s++;
  return pw.length < 10 ? Math.min(s, 1) || 1 : Math.max(2, s);
}
const NOTES = ["At least 10 characters, with a number or symbol.", "Too short. Use at least 10 characters.", "Okay. Add length or a symbol to strengthen it.", "Good.", "Strong."];
$("su-pw").addEventListener("input", () => {
  const lv = strength($("su-pw").value);
  $("strength").dataset.level = lv;
  $("strengthNote").textContent = NOTES[lv];
  if ($("g-pw").classList.contains("bad") && lv >= 2) fieldErr("pw", "");
});
$("forgot").onclick = () => $("resetHelp").classList.toggle("show");

/* ---------- create account ---------- */
$("formUp").onsubmit = async (e) => {
  e.preventDefault();
  showErr("upError", "");
  const name = $("su-name").value.trim(), email = $("su-email").value.trim(), pw = $("su-pw").value;
  fieldErr("name", name ? "" : "Enter your name.");
  fieldErr("email", EMAIL_RE.test(email) ? "" : "Enter a valid email address.");
  fieldErr("pw", pw.length >= 10 ? "" : "Use at least 10 characters.");
  if (!name || !EMAIL_RE.test(email) || pw.length < 10) return;
  if (!$("su-terms").checked) return showErr("upError", "Please confirm that you will only audit sites you are allowed to test.");
  busy($("upBtn"), true, "Creating account…");
  try {
    PD.user = await post("/api/auth/signup", { name, email, password: pw, remember: true });
    startWizard(true);
  } catch (err) {
    if (err.status === 409 || /valid email/i.test(err.message)) fieldErr("email", err.message);
    else if (/password|character|guess|symbol|number|email name/i.test(err.message)) fieldErr("pw", err.message);
    else showErr("upError", err.message);
  }
  busy($("upBtn"), false);
};

/* ---------- sign in ---------- */
$("formIn").onsubmit = async (e) => {
  e.preventDefault();
  showErr("inError", "");
  const email = $("si-email").value.trim(), pw = $("si-pw").value;
  if (!email || !pw) return showErr("inError", "Enter your email and password.");
  busy($("inBtn"), true, "Signing in…");
  try {
    PD.user = await post("/api/auth/login", { email, password: pw, remember: $("si-remember").checked });
    if (PD.user.onboarded) location.href = "/app";
    else startWizard(false);
  } catch (err) {
    showErr("inError", err.message);
    busy($("inBtn"), false);
  }
};

/* ---------- demo / guest ---------- */
async function guest(btn) {
  busy(btn, true, "Starting demo…");
  try { await post("/api/auth/guest"); location.href = "/app"; }
  catch (err) { showErr(btn === $("guestBtn") ? "upError" : "inError", err.message); busy(btn, false); }
}
$("guestBtn").onclick = () => guest($("guestBtn"));
$("guestBtn2").onclick = () => guest($("guestBtn2"));

/* ---------- onboarding wizard ---------- */
$("wz-country").innerHTML = '<option value="">Select your country</option>' + Object.entries(COUNTRY_NAMES).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
$("ucChips").innerHTML = Object.entries(USE_CASES).map(([k, v]) => `<input type="checkbox" id="uc-${k}" value="${k}"><label for="uc-${k}">${v}</label>`).join("");
try { const region = (navigator.language || "").split("-")[1]; if (COUNTRY_NAMES[region]) $("wz-country").value = region; } catch (e) { /* ignore */ }

function startWizard(isNew) {
  $("viewAuth").hidden = true;
  $("viewWizard").hidden = false;
  $("welcomeTitle").textContent = (isNew ? "Welcome, " : "Welcome back, ") + firstName(PD.user);
  stepTo(2);
  $("auth").scrollIntoView({ block: "center" });
}
function stepTo(n) {
  $("wAbout").hidden = n !== 2;
  $("wPrefs").hidden = n !== 3;
  [1, 2, 3].forEach((i) => { const s = $("st" + i); s.classList.toggle("done", i < n); s.classList.toggle("now", i === n); });
  const first = (n === 2 ? $("wz-country") : document.querySelector('input[name="wzprov"]:checked'));
  if (first) first.focus({ preventScroll: true });
}
$("wAbout").onsubmit = (e) => { e.preventDefault(); stepTo(3); };
$("skipAbout").onclick = () => stepTo(3);
$("backPrefs").onclick = () => stepTo(2);
document.querySelectorAll('input[name="wztheme"]').forEach((r) => (r.onchange = () => PD.setTheme(r.value)));  // live preview

$("wPrefs").onsubmit = async (e) => {
  e.preventDefault();
  showErr("prefsError", "");
  busy($("finishBtn"), true, "Saving…");
  try {
    await patch("/api/profile", {
      country: $("wz-country").value || "",
      city: $("wz-city").value.trim(),
      use_cases: [...document.querySelectorAll("#ucChips input:checked")].map((i) => i.value),
      complaint_name: $("wz-cname").value.trim() || PD.user.name,
      default_provider: document.querySelector('input[name="wzprov"]:checked').value,
      default_quick: $("wz-quick").checked,
      theme: document.querySelector('input[name="wztheme"]:checked').value,
      onboarded: true,
    });
    location.href = "/app";
  } catch (err) {
    showErr("prefsError", err.message);
    busy($("finishBtn"), false);
  }
};

/* ---------- already signed in but setup unfinished (e.g. closed the tab mid-wizard) ---------- */
PD.ready.then((u) => { if (u && !u.onboarded) startWizard(false); });

/* ---------- the 3D stage (falls back to a static preview if WebGL is unavailable) ---------- */
try {
  const stage = Scene3D.webglOK()
    ? Scene3D.PriceStack($("stageCanvas"), { stageUi: $("stageUi"), pay: $("stPay"), drip: $("stDrip"), dripWrap: $("stDripWrap") })
    : null;
  if (stage) $("stReplay").onclick = () => stage.replay();
  else throw new Error("no webgl");
} catch (e) {
  $("stageCanvas").hidden = true; $("stageUi").hidden = true; $("stageFallback").hidden = false;
}
