"use strict";
/* Profile page: account, complaint details, preferences, security, audit history, deletion. */

const COUNTRY_NAMES = { IN: "India", US: "United States", GB: "United Kingdom", CA: "Canada", DE: "Germany", FR: "France", JP: "Japan", AU: "Australia", SG: "Singapore", AE: "United Arab Emirates", OTHER: "Somewhere else" };
const USE_CASES = { shopping: "Online shopping", travel: "Travel and flights", tickets: "Event tickets", subscriptions: "Subscriptions", software: "Software and SaaS", food: "Food delivery" };

const flash = (id, text) => { const el = $(id); el.textContent = text; clearTimeout(el._t); el._t = setTimeout(() => (el.textContent = ""), 3500); };
const showErr = (id, msg) => { const el = $(id); el.textContent = msg || ""; el.classList.toggle("show", !!msg); };
const busy = (btn, on, text) => { btn.disabled = on; if (on) { btn.dataset.t = btn.textContent; btn.textContent = text; } else if (btn.dataset.t) btn.textContent = btn.dataset.t; };
const scoreColor = (s) => (s >= 75 ? "var(--good)" : s >= 45 ? "var(--warn)" : s >= 25 ? "var(--serious)" : "var(--crit)");

/* ---------- hero + forms ---------- */
function renderHero(u) {
  $("heroAvatar").innerHTML = avatarHtml(u, "xl");
  $("heroName").innerHTML = `${esc(u.name)} <span class="badge">${u.is_guest ? "Demo session" : "Member"}</span>`;
  const bits = [];
  bits.push(u.is_guest ? "No email on file" : esc(u.email));
  const place = [u.city, COUNTRY_NAMES[u.country]].filter(Boolean).join(", ");
  if (place) bits.push(esc(place));
  bits.push(`${u.is_guest ? "Started" : "Member since"} ${fmtDate(u.created_at)}`);
  $("heroMeta").innerHTML = bits.map((b) => `<span>${b}</span>`).join("");
}

function fillForms(u) {
  $("pf-name").value = u.name;
  $("pf-email").value = u.is_guest ? "Demo session (no email)" : u.email;
  $("pf-country").innerHTML = '<option value="">Not set</option>' + Object.entries(COUNTRY_NAMES).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
  $("pf-country").value = u.country;
  $("pf-city").value = u.city;
  $("pf-cname").value = u.complaint_name;
  $("pf-uc").innerHTML = Object.entries(USE_CASES).map(([k, v]) => `<input type="checkbox" id="puc-${k}" value="${k}" ${u.use_cases.includes(k) ? "checked" : ""}><label for="puc-${k}">${v}</label>`).join("");
  document.querySelector(`input[name="pfprov"][value="${u.default_provider}"]`).checked = true;
  document.querySelector(`input[name="pftheme"][value="${u.theme}"]`).checked = true;
  $("pf-quick").checked = u.default_quick;
  $("guestBanner").hidden = !u.is_guest;
  $("formPw").hidden = u.is_guest;
  $("pwGuest").hidden = !u.is_guest;
  $("revokeOthers").hidden = u.is_guest;
  $("delPwGroup").hidden = u.is_guest;
  if (u.is_guest) { $("delOpen").textContent = "End demo session and delete its data"; }
}

async function save(data, errId, okId, btn) {
  showErr(errId, "");
  busy(btn, true, "Saving…");
  try {
    PD.user = await patch("/api/profile", data);
    renderHero(PD.user);
    flash(okId, "Saved");
    return PD.user;
  } catch (err) { showErr(errId, err.message); }
  finally { busy(btn, false); }
}

$("formAccount").onsubmit = (e) => {
  e.preventDefault();
  if (!$("pf-name").value.trim()) return showErr("accErr", "Enter your name.");
  save({ name: $("pf-name").value.trim(), country: $("pf-country").value, city: $("pf-city").value.trim() }, "accErr", "accOk", e.submitter);
};
$("formComplaint").onsubmit = (e) => {
  e.preventDefault();
  save({ complaint_name: $("pf-cname").value.trim(), use_cases: [...document.querySelectorAll("#pf-uc input:checked")].map((i) => i.value) }, "cmpErr", "cmpOk", e.submitter);
};
document.querySelectorAll('input[name="pftheme"]').forEach((r) => (r.onchange = () => PD.setTheme(r.value)));  // live preview
$("formPrefs").onsubmit = async (e) => {
  e.preventDefault();
  const u = await save({
    default_provider: document.querySelector('input[name="pfprov"]:checked').value,
    default_quick: $("pf-quick").checked,
    theme: document.querySelector('input[name="pftheme"]:checked').value,
  }, "prefErr", "prefOk", e.submitter);
  if (u) PD.setTheme(u.theme);
};

/* ---------- stats ---------- */
async function loadStats() {
  const s = await api("/api/profile/stats");
  const tile = (label, value, sub, c) => `<div class="kpi"><div class="k-label">${c ? `<i class="sev-mark" style="--c:${c}"></i>` : ""}${esc(label)}</div><div class="k-value">${esc(value)}</div><div class="k-sub">${sub}</div></div>`;
  if (!s.audits) {
    $("stats").innerHTML = tile("Audits run", "0", 'Run your first one from the <a class="link" href="/app">console</a>') + tile("Average transparency", "n/a", "Appears after your first audit") + tile("Findings logged", "0", "Across all your audits") + tile("Lowest score", "n/a", "The least transparent site you audited");
    return;
  }
  $("stats").innerHTML =
    tile("Audits run", String(s.audits), `Last: ${esc(s.last.site)}`) +
    tile("Average transparency", Math.round(s.avg_score) + " / 100", "Across your audits", scoreColor(s.avg_score)) +
    tile("Findings logged", String(s.findings), "Measured, with evidence") +
    tile("Lowest score", String(Math.round(s.worst.score)), `<a class="link" href="/app?audit=${encodeURIComponent(s.worst.audit_id)}">${esc(s.worst.site)}</a>`, scoreColor(s.worst.score));
}

/* ---------- password ---------- */
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
$("pw-new").addEventListener("input", () => { const lv = strength($("pw-new").value); $("strength").dataset.level = lv; $("strengthNote").textContent = NOTES[lv]; });
$("pwShow").onclick = () => { const i = $("pw-new"), show = i.type === "password"; i.type = show ? "text" : "password"; $("pwShow").textContent = show ? "Hide" : "Show"; };
$("formPw").onsubmit = async (e) => {
  e.preventDefault();
  showErr("pwErr", "");
  const cur = $("pw-cur").value, nw = $("pw-new").value;
  if (!cur) return showErr("pwErr", "Enter your current password.");
  if (nw.length < 10) return showErr("pwErr", "Your new password needs at least 10 characters.");
  busy($("pwBtn"), true, "Updating…");
  try {
    await post("/api/profile/password", { current: cur, new: nw });
    $("pw-cur").value = $("pw-new").value = "";
    $("strength").dataset.level = 0; $("strengthNote").textContent = NOTES[0];
    flash("pwOk", "Password updated. Other devices were signed out.");
    loadSessions();
  } catch (err) { showErr("pwErr", err.message); }
  busy($("pwBtn"), false);
};

/* ---------- sessions ---------- */
async function loadSessions() {
  const list = await api("/api/profile/sessions");
  $("sessions").innerHTML = list.map((s) => `
    <div class="sess"><div><b>${esc(describeUA(s.ua))}${s.current ? '<span class="badge here">This device</span>' : ""}</b><div class="m">${esc(s.ip)} · active ${fmtDate(s.last_seen, { dateStyle: "medium", timeStyle: "short" })}</div></div>
    ${s.current ? "" : `<button class="btn" type="button" data-revoke="${s.id}">Sign out</button>`}</div>`).join("") || '<p class="muted small">No active sessions.</p>';
  document.querySelectorAll("[data-revoke]").forEach((b) => (b.onclick = async () => { await del(`/api/profile/sessions/${b.dataset.revoke}`); loadSessions(); }));
}
$("revokeOthers").onclick = async () => { await post("/api/profile/sessions/revoke-others"); loadSessions(); };
$("signoutHere").onclick = async () => { try { await post("/api/auth/logout"); } finally { location.href = "/"; } };

/* ---------- audit history ---------- */
async function loadHistory() {
  const mine = (await api("/api/audits")).filter((a) => a.mine);
  if (!mine.length) {
    $("histBody").innerHTML = '<div class="pf-empty">No audits yet. <a class="link" href="/app">Run your first audit</a> and it will appear here.</div>';
    return;
  }
  $("histBody").innerHTML = `<div class="tablewrap"><table class="data"><thead><tr><th>Site</th><th>Transparency</th><th class="r">Findings</th><th>Date</th><th></th></tr></thead><tbody>` +
    mine.map((a) => `<tr><td><b style="font-weight:580">${esc(a.site)}</b><div class="muted small">${esc(a.provider)}</div></td>
      <td><div class="score-cell"><b>${Math.round(a.trust_score)}</b><span class="meter" style="--m:${scoreColor(a.trust_score)}"><i style="width:${Math.max(3, a.trust_score)}%"></i></span></div></td>
      <td class="r">${a.findings}</td><td>${fmtDate(a.created_at, { dateStyle: "medium", timeStyle: "short" })}</td>
      <td><div class="hist-actions"><a class="btn" href="/app?audit=${encodeURIComponent(a.audit_id)}">Open</a><button class="btn" type="button" data-del="${esc(a.audit_id)}">Delete</button></div></td></tr>`).join("") + "</tbody></table></div>";
  document.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => {
    if (b.dataset.arm !== "1") {  // two-step delete without window.confirm
      b.dataset.arm = "1"; b.textContent = "Confirm"; b.classList.add("danger");
      setTimeout(() => { b.dataset.arm = ""; b.textContent = "Delete"; b.classList.remove("danger"); }, 3500);
      return;
    }
    await del(`/api/audits/${encodeURIComponent(b.dataset.del)}`);
    loadHistory(); loadStats();
  }));
}

/* ---------- delete account ---------- */
$("delOpen").onclick = () => { $("formDel").hidden = false; $("delOpen").hidden = true; (PD.user.is_guest ? $("delBtn") : $("del-pw")).focus(); };
$("delCancel").onclick = () => { $("formDel").hidden = true; $("delOpen").hidden = false; showErr("delErr", ""); $("del-pw").value = ""; };
$("formDel").onsubmit = async (e) => {
  e.preventDefault();
  showErr("delErr", "");
  if (!PD.user.is_guest && !$("del-pw").value) return showErr("delErr", "Enter your password to confirm.");
  busy($("delBtn"), true, "Deleting…");
  try { await del("/api/profile", { password: $("del-pw").value }); location.href = "/"; }
  catch (err) { showErr("delErr", err.message); busy($("delBtn"), false); }
};

/* ---------- section nav highlight ---------- */
function setupNav() {
  const links = [...document.querySelectorAll("#pfNav a")];
  const io = new IntersectionObserver((entries) => {
    entries.forEach((en) => { if (en.isIntersecting) links.forEach((l) => l.classList.toggle("on", l.getAttribute("href") === "#" + en.target.id)); });
  }, { rootMargin: "-20% 0px -65% 0px" });
  links.forEach((l) => io.observe(document.querySelector(l.getAttribute("href"))));
}

(async function boot() {
  const user = await bootSignedIn("profile");
  if (!user) return;
  renderHero(user);
  fillForms(user);
  setupNav();
  await Promise.all([loadStats(), loadSessions(), loadHistory()]);
})();
