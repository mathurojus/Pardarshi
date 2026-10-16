"use strict";
/* Shared by every page: helpers, API client, theme, and the signed-in app bar. Loaded before page scripts. */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const cssVar = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

/* ---------- API client ---------- */
const api = async (path, opts = {}) => {
  const r = await fetch(path, { credentials: "same-origin", ...opts, headers: { "X-Requested-With": "pardarshi", ...(opts.headers || {}) } });
  if (r.status === 401 && location.pathname !== "/" && !opts.noRedirect) {
    // Only an expired/missing session means "go sign in". Other 401s (a wrong password) must show their message.
    let d = {};
    try { d = await r.clone().json(); } catch (e) { /* not JSON */ }
    if (d.detail === "Please sign in.") {
      location.href = "/";
      throw new Error("Please sign in.");
    }
  }
  if (!r.ok) {
    let d = {};
    try { d = await r.json(); } catch (e) { /* not JSON */ }
    const msg = typeof d.detail === "string" ? d.detail : Array.isArray(d.detail) ? d.detail.map((x) => x.msg).join(" ") : r.statusText;
    const err = new Error(msg || "Something went wrong.");
    err.status = r.status;
    throw err;
  }
  return r.json();
};
const send = (method) => (path, body, opts = {}) => api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}), ...opts });
const post = send("POST"), patch = send("PATCH"), del = send("DELETE");

/* ---------- theme ---------- */
const THEME_ICON = {
  sun: '<svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  moon: '<svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
};
const PD = {
  user: null,
  _themeCbs: [],
  onTheme(cb) { this._themeCbs.push(cb); },
  currentTheme() { return document.documentElement.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"); },
  /** mode: "system" | "light" | "dark" */
  setTheme(mode) {
    if (mode === "light" || mode === "dark") document.documentElement.setAttribute("data-theme", mode);
    else document.documentElement.removeAttribute("data-theme");
    try { mode === "system" ? localStorage.removeItem("pardarshi-theme") : localStorage.setItem("pardarshi-theme", mode); } catch (e) { /* storage blocked */ }
    this.paintThemeButton();
    this._themeCbs.forEach((cb) => cb());
  },
  paintThemeButton() { const b = $("theme"); if (b) b.innerHTML = this.currentTheme() === "dark" ? THEME_ICON.sun : THEME_ICON.moon; },
  async loadMe() {
    try { this.user = await api("/api/auth/me", { noRedirect: true }); } catch (e) { this.user = null; }
    return this.user;
  },
};
PD.ready = PD.loadMe();
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { PD.paintThemeButton(); PD._themeCbs.forEach((cb) => cb()); });

/* ---------- avatar + formatting ---------- */
const avatarHtml = (u, cls = "") => `<span class="avatar av${u.hue % 6} ${cls}" aria-hidden="true">${esc(u.initials)}</span>`;
const fmtDate = (epochOrIso, opts = { dateStyle: "medium" }) => new Date(typeof epochOrIso === "number" ? epochOrIso * 1000 : epochOrIso).toLocaleString([], opts);
function describeUA(ua) {
  const os = /Windows/.test(ua) ? "Windows" : /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS" : /Mac OS/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : "Unknown OS";
  const br = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : "Browser";
  return `${br} on ${os}`;
}

/* ---------- app bar (signed-in pages) ---------- */
function mountAppbar(active) {
  const u = PD.user;
  const link = (href, label, key) => `<a href="${href}" class="nav-link" ${active === key ? 'aria-current="page"' : ""}>${label}</a>`;
  $("appbar").innerHTML = `
    <a class="brand" href="/app" aria-label="Pardarshi console">
      <svg class="mark" width="36" height="36" viewBox="0 0 64 64" aria-hidden="true"><rect class="mk-tile" width="64" height="64" rx="16" fill="#27358f"/><circle cx="34" cy="26" r="12" fill="#fff" fill-opacity=".10"/><path d="M28.5 22.5 H39.5" stroke="#fff" stroke-opacity=".62" stroke-width="2.4" stroke-linecap="round"/><path d="M28.5 28.5 H36" stroke="#f0b429" stroke-width="3.2" stroke-linecap="round"/><circle cx="34" cy="26" r="12" fill="none" stroke="#fff" stroke-width="5"/><path d="M22 26 V50" stroke="#fff" stroke-width="5.5" stroke-linecap="round"/></svg>
      <span class="brand-text"><b>Pardarshi</b><small><span class="deva" lang="hi">पारदर्शी</span> · Agents that shop like you</small></span>
    </a>
    <nav class="nav" aria-label="Primary">${link("/app", "Audit console", "app")}${link("/profile", "Profile", "profile")}</nav>
    <div class="appbar-right">
      <div id="health" class="sys" aria-label="System status"></div>
      <button id="theme" class="icon-btn" type="button" aria-label="Switch colour theme" title="Switch colour theme"></button>
      <div class="usermenu">
        <button id="umBtn" class="um-btn" type="button" aria-haspopup="menu" aria-expanded="false" aria-controls="umPanel">
          ${avatarHtml(u)}<span class="um-name">${esc(u.name.split(" ")[0])}</span><i class="um-chev" aria-hidden="true"></i>
        </button>
        <div id="umPanel" class="um-panel" role="menu" hidden>
          <div class="um-head">${avatarHtml(u, "lg")}<div><b>${esc(u.name)}</b><span>${u.is_guest ? "Demo session" : esc(u.email)}</span></div></div>
          <a role="menuitem" href="/profile">Profile and settings</a>
          <a role="menuitem" href="/app">Audit console</a>
          ${u.is_guest ? '<a role="menuitem" href="/?upgrade=1">Create an account</a>' : ""}
          <button role="menuitem" id="signout" type="button">Sign out</button>
        </div>
      </div>
    </div>`;
  PD.paintThemeButton();
  const bar = $("appbar");
  const onScroll = () => bar.classList.toggle("scrolled", scrollY > 4);
  addEventListener("scroll", onScroll, { passive: true }); onScroll();
  $("theme").onclick = () => {
    const next = PD.currentTheme() === "dark" ? "light" : "dark";
    PD.setTheme(next);
    if (!PD.user.is_guest) patch("/api/profile", { theme: next }).catch(() => {});
  };
  const btn = $("umBtn"), panel = $("umPanel");
  const close = () => { panel.hidden = true; btn.setAttribute("aria-expanded", "false"); };
  btn.onclick = (e) => { e.stopPropagation(); panel.hidden = !panel.hidden; btn.setAttribute("aria-expanded", String(!panel.hidden)); if (!panel.hidden) panel.querySelector("a,button").focus(); };
  document.addEventListener("click", (e) => { if (!panel.contains(e.target)) close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !panel.hidden) { close(); btn.focus(); } });
  $("signout").onclick = async () => { try { await post("/api/auth/logout"); } finally { location.href = "/"; } };
}

/* A signed-in page must have a user; apply their saved theme preference, then run the page. */
async function bootSignedIn(active) {
  const u = await PD.ready;
  if (!u) { location.href = "/"; return null; }
  if (u.theme && u.theme !== "system") PD.setTheme(u.theme);
  mountAppbar(active);
  return u;
}
