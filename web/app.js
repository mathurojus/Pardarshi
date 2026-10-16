"use strict";
/* Pardarshi dashboard. Vanilla JS, hand-built SVG charts (no chart library), so every mark follows the
   same spec: thin bars, 4px rounded data end, 2px gaps, hairline grid, hover tooltip, table twin. */

/* Helpers ($, esc, api, post, cssVar, PD) come from common.js. */
const NS = "http://www.w3.org/2000/svg";
const sv = (tag, attrs = {}, kids = []) => {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) n.setAttribute(k, v);
  for (const c of [].concat(kids)) n.append(c);
  return n;
};
const SYMBOL = { INR: "₹", USD: "$", EUR: "€", GBP: "£", JPY: "¥", "": "₹" };
const money = (n, cur) => (n == null ? "n/a" : `${SYMBOL[cur] ?? cur + " "}${Math.round(n).toLocaleString("en-IN")}`);
const axisMoney = (n, cur) => `${SYMBOL[cur] ?? ""}${n >= 1000 ? +(n / 1000).toFixed(1) + "K" : Math.round(n)}`;
const pct = (n, d = 1) => `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toFixed(d)}%`;
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const approxW = (s, size) => String(s).length * size * 0.56;

const PERSONA = { base: "Baseline", iphone: "iPhone", android: "Android", returning: "Returning visitor" };
const COUNTRY = { US: "United States", GB: "United Kingdom", CA: "Canada", DE: "Germany", FR: "France", JP: "Japan", AU: "Australia" };
const LEVEL = { iphone: "iPhone", android: "Android", desktop: "Desktop", returning: "Returning visitor", new: "New visitor" };
const levelName = (l) => COUNTRY[l] || LEVEL[l] || cap(String(l));
const personaName = (w) => PERSONA[w.persona_id] || (w.persona_id.startsWith("c_") ? COUNTRY[w.persona_id.slice(2).toUpperCase()] || cap(w.persona_id) : cap(w.persona_id));

const ICON = {
  check: '<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="m8 12.3 2.8 2.8L16 9.6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  alert: '<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5 2.8 19.5h18.4L12 3.5Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M12 10v4.2M12 17.2v.1" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
};

/* ---------- state ---------- */
const S = { report: null, wfView: "chart", effView: "chart", sev: "all", chatSession: null };

/* ---------- theme: the toggle lives in the shared app bar; charts only need to redraw ---------- */
PD.onTheme(() => { if (S.report) drawCharts(); });

/* ---------- status mapping (score -> word, colour, icon) ---------- */
function scoreStatus(s) {
  if (s >= 75) return { word: "Transparent", c: "var(--good)", icon: ICON.check };
  if (s >= 45) return { word: "Mixed", c: "var(--warn)", icon: ICON.alert };
  if (s >= 25) return { word: "Concerning", c: "var(--serious)", icon: ICON.alert };
  return { word: "Poor", c: "var(--crit)", icon: ICON.alert };
}

/* ---------- tooltip (one element; values via textContent, never innerHTML) ---------- */
const tip = $("tip");
function tipShow(spec, x, y) {
  const mk = (cls, text) => { const d = document.createElement("div"); d.className = cls; if (text != null) d.textContent = text; return d; };
  const nodes = [mk("t-title", spec.title)];
  for (const r of spec.rows) {
    const row = mk("t-row");
    if (r.c) { const ln = document.createElement("i"); ln.className = "ln"; ln.style.setProperty("--c", r.c); row.append(ln); }
    row.append(mk("n", r.name)); row.append(mk("v", r.value));
    nodes.push(row);
  }
  if (spec.foot) nodes.push(mk("t-foot", spec.foot));
  tip.replaceChildren(...nodes);
  tip.hidden = false;
  const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
  let left = x + pad, top = y + pad;
  if (left + w > innerWidth - 8) left = x - w - pad;
  if (top + h > innerHeight - 8) top = innerHeight - h - 8;
  tip.style.left = Math.max(8, left) + "px";
  tip.style.top = Math.max(8, top) + "px";
}
const tipHide = () => { tip.hidden = true; };
function bindTip(node, specFn) {
  node.addEventListener("pointermove", (e) => tipShow(specFn(), e.clientX, e.clientY));
  node.addEventListener("pointerleave", tipHide);
  node.addEventListener("focus", () => { const r = node.getBoundingClientRect(); tipShow(specFn(), r.left + r.width / 2, r.top + r.height / 2); });
  node.addEventListener("blur", tipHide);
}

/* ---------- in-page dialog for a custom host (no window.prompt) ---------- */
function askHost() {
  return new Promise(async (resolve) => {
    const conn = await api("/api/sessions").catch(() => []);
    const guest = PD.user && PD.user.is_guest;
    const list = conn.length
      ? `<ul class="conn-list">${conn.map((c) => `<li><span class="mono">${esc(c.host)}</span><small>saved ${esc(fmtDate(c.saved_at))}</small><button class="link" type="button" data-forget="${esc(c.host)}">Forget</button></li>`).join("")}</ul>`
      : `<p class="muted small">No shop accounts connected yet.</p>`;
    $("modalBody").innerHTML = `<h2 style="font-size:18px;margin-bottom:4px">Audit another site</h2>
      <p class="muted small" style="margin-bottom:14px">Only hosts on the server's allowlist (<span class="mono">ALLOWED_HOSTS</span>) can be audited. Agents never pay, never place an order and never type personal details. If a site asks for a login first, the run stops there and uses the furthest checkout total it saw.</p>
      <div class="fgroup" style="margin-bottom:12px"><label for="hostIn">Hostname or URL</label><input class="input" id="hostIn" placeholder="shop.example.com" autocomplete="off"></div>
      <label class="check" style="margin-bottom:6px"><input type="checkbox" id="useSess" ${guest ? "disabled" : ""}><span><b>Use my signed-in session</b> for this site: audit behind the login with your own account. One shopper, one run, on the Open source provider. ${guest ? "Needs an account." : ""}</span></label>
      <details class="conn"><summary>Connected shop accounts (${conn.length})</summary>${list}
        <p class="muted small">Type the site above, then click <b>Connect account</b>. Your own Chrome opens on that site in a normal window; sign in there yourself (password, OTP), then come back and click <b>Save sign-in</b>. Pardarshi never sees what you type; it saves only the cookies, under your account.</p>
        <div class="conn-flow" id="connFlow"><button class="btn ghost" id="connGo" type="button" ${guest ? "disabled" : ""}>Connect account</button></div></details>
      <div class="modal-actions"><button class="btn primary" id="hostGo" type="button">Start audit</button><button class="btn ghost" id="hostNo" type="button">Cancel</button></div>`;
    const dlg = $("modal");
    const finish = (v) => { dlg.removeEventListener("close", onClose); dlg.close(); resolve(v); };
    const onClose = () => resolve(null);
    dlg.addEventListener("close", onClose, { once: true });
    dlg.showModal();
    $("hostIn").focus();
    $("hostGo").onclick = () => { const h = $("hostIn").value.trim(); finish(h ? { site: h, useSession: $("useSess").checked } : null); };
    $("hostNo").onclick = () => finish(null);
    $("hostIn").onkeydown = (e) => { if (e.key === "Enter") $("hostGo").click(); };
    const flow = $("connFlow");
    const connStart = async () => {
      const h = $("hostIn").value.trim();
      if (!h) { $("hostIn").focus(); flow.insertAdjacentHTML("afterbegin", '<p class="small" style="margin:0 0 6px">Type the site above first, e.g. www.flipkart.com</p>'); return; }
      flow.innerHTML = `<span class="muted small">Opening a browser window…</span>`;
      try {
        const r = await post(`/api/sessions/${encodeURIComponent(h)}/connect`);
        flow.innerHTML = `<p class="small" style="margin:0 0 8px">A ${esc(r.browser === "msedge" ? "Edge" : "Chrome")} window is open on <span class="mono">${esc(r.host)}</span>. Sign in there, then:</p>
          <button class="btn primary" id="connSave" type="button">Save sign-in</button> <button class="btn ghost" id="connCancel" type="button">Cancel</button>`;
        $("connSave").onclick = async () => {
          $("connSave").disabled = true;
          try {
            const s = await post(`/api/sessions/${encodeURIComponent(r.host)}/save`);
            flow.innerHTML = `<p class="small" style="margin:0">Saved ${esc(s.cookies)} cookies for <span class="mono">${esc(s.host)}</span>. “Use my signed-in session” is now ticked.</p>`;
            $("useSess").checked = true;
          } catch (e) { $("connSave").disabled = false; flow.querySelector("p").textContent = e.message; }
        };
        $("connCancel").onclick = async () => {
          await post(`/api/sessions/${encodeURIComponent(r.host)}/cancel`).catch(() => {});
          flow.innerHTML = `<button class="btn ghost" id="connGo" type="button">Connect account</button>`;
          $("connGo").onclick = connStart;
        };
      } catch (e) {
        flow.innerHTML = `<p class="small" style="margin:0 0 8px">${esc(e.message)}</p><button class="btn ghost" id="connGo" type="button">Try again</button>`;
        $("connGo").onclick = connStart;
      }
    };
    if ($("connGo")) $("connGo").onclick = connStart;
    dlg.querySelectorAll("[data-forget]").forEach((b) => (b.onclick = async () => {
      await del(`/api/sessions/${encodeURIComponent(b.dataset.forget)}`).catch(() => {});
      b.closest("li").remove();
    }));
  });
}

/* ---------- header, toolbar ---------- */
async function loadHealth() {
  try {
    const h = await api("/api/health/details");
    const item = (ok, t) => `<span class="sys-item"><i class="dot ${ok ? "" : "off"}"></i>${esc(t)}</span>`;
    $("health").innerHTML = item(true, h.provider) + item(h.tinyfish_key, "TinyFish key") + item(h.gemini_key, "Gemini") + item(h.classifier_trained, "Classifier") + item(h.demo_shop_up, "Demo shop");
  } catch (e) { $("health").innerHTML = '<span class="sys-item"><i class="dot off"></i>Backend offline</span>'; }
}
async function loadSites() {
  const sites = await api("/api/sites");
  const opt = (s) => `<option value="${esc(s.id)}">${esc(s.label)}</option>`;
  const demo = sites.filter((s) => s.kind === "demo"), real = sites.filter((s) => s.kind !== "demo");
  $("site").innerHTML = `<optgroup label="Demo shops">${demo.map(opt).join("")}</optgroup>`
    + (real.length ? `<optgroup label="Real sites (allowlisted)">${real.map(opt).join("")}</optgroup>` : "")
    + '<option value="__custom">Another site, or use my signed-in session…</option>';
}

$("run").onclick = async () => {
  let site = $("site").value, useSession = false;
  if (site === "__custom") {
    const pick = await askHost();
    if (!pick) return;
    ({ site, useSession } = pick);
  }
  const provider = document.querySelector('input[name="provider"]:checked').value || null;
  $("run").disabled = true;
  $("report").classList.add("stale");
  $("progress").hidden = false;
  $("agents").replaceChildren();
  $("pinned").hidden = true;
  $("inconclusive").hidden = true;
  $("barfill").style.width = "3%";
  $("progressTitle").textContent = "Starting agents";
  $("progressCount").textContent = "";
  try {
    const { audit_id } = await post("/api/audits", { site, provider, quick: $("quick").checked, use_session: useSession });
    watch(audit_id);
  } catch (e) {
    $("progressTitle").textContent = "Could not start: " + e.message;
    $("run").disabled = false;
    $("report").classList.remove("stale");
  }
};

/* ---------- live agents: one card per shopper and flow, one pip per repeat, the current step as it happens ---------- */
const STEP_TEXT = { event: "Reading the product page", cart: "Reviewing the basket", payment: "At payment, not paying",
  signed_up: "Signed up for a trial", confirm: "Confirming cancellation", done: "Finished" };
const stepText = (s) => STEP_TEXT[s] || cap(String(s).replace(/_/g, " "));
const AG = { cards: new Map(), pips: new Map() };

function buildAgents(jobs) {
  const box = $("agents");
  box.replaceChildren();
  AG.cards.clear(); AG.pips.clear();
  for (const j of jobs) {
    const g = `${j.flow}:${j.persona_id}`;
    if (!AG.cards.has(g)) {
      const name = personaName(j);
      const el = document.createElement("div");
      el.className = "agent";
      el.innerHTML = `<div class="ag-head"><span class="ag-av" aria-hidden="true">${esc(name.charAt(0))}</span><div class="ag-who"><b>${esc(name)}</b><small>${esc(j.persona.replace(/\//g, " · "))}</small></div><span class="ag-flow">${j.flow === "cancel" ? "cancel" : "buy"}</span></div>
        <div class="ag-step"><i class="ag-dot"></i><span>Queued</span></div>
        <div class="ag-prod" hidden></div>
        <div class="ag-foot"><span class="ag-pips"></span><b class="ag-price"></b></div>`;
      box.append(el);
      AG.cards.set(g, el);
    }
    const pip = document.createElement("i");
    pip.title = `Run ${j.repeat + 1}`;
    AG.cards.get(g).querySelector(".ag-pips").append(pip);
    AG.pips.set(j.key, { pip, card: AG.cards.get(g) });
  }
}
function agentStep(key, step) {
  const a = AG.pips.get(key);
  if (!a) return;
  a.pip.className = "run";
  a.card.classList.add("live");
  const span = a.card.querySelector(".ag-step span");
  span.textContent = stepText(step);
  span.classList.remove("tick"); void span.offsetWidth; span.classList.add("tick");
}
function agentDone(e) {
  const a = AG.pips.get(e.key);
  if (!a) return;
  a.pip.className = e.ok ? "ok" : "bad";
  if (e.final_price) a.card.querySelector(".ag-price").textContent = money(e.final_price, "INR");
  if (e.product && e.product.name) {
    const el = a.card.querySelector(".ag-prod");
    el.hidden = false; el.textContent = e.product.name; el.title = e.product.url || "";
  }
  if (e.error) a.card.querySelector(".ag-step span").textContent = "Blocked: " + e.error.slice(0, 60);
  const pips = [...a.card.querySelectorAll(".ag-pips i")];
  if (pips.every((p) => p.className === "ok" || p.className === "bad")) {
    a.card.classList.remove("live");
    a.card.classList.add(pips.some((p) => p.className === "bad") ? "bad" : "ok");
    if (!e.error) a.card.querySelector(".ag-step span").textContent = e.flow === "cancel" ? "Cancellation measured"
      : ({ payment: "Stopped at payment", price_screen: "Stopped at login, total recorded", login: "Login wall before any total" })[e.stopped_at] || "Stopped at payment";
  }
}

/* ---------- nothing measured: say why, show the page that stopped the shoppers, and what to try ---------- */
const NEXT = {
  blocked: ["Audit it with your own signed-in session: choose “Another site, or use my signed-in session…”. Signed-in shoppers are challenged far less.",
    "Try the TinyFish provider: its managed cloud browsers are blocked less often than a local one.",
    "Try again later. Many blocks are short rate limits, and real sites already run one shopper at a time, at a human pace.",
    "Keep this result: a site that blocks every independent check is itself worth recording."],
  login: ["Connect your own account: choose “Another site, or use my signed-in session…”, click Connect account and sign in in the window that opens.",
    "Check whether the site shows its price details before login on another page (for example the cart)."],
  other: ["Try again, or try the TinyFish provider.", "Check the screenshot: the agent may need a clearer way to the cart on this site."],
};
function showInconclusive(e) {
  const title = { blocked: "Blocked by the site's bot protection", login: "Stopped at a login before any price", other: "Nothing could be measured" }[e.reason] || "Nothing could be measured";
  $("progressTitle").textContent = "Audit could not measure this site";
  const box = $("inconclusive");
  box.className = `inconclusive r-${esc(e.reason)}`;
  box.innerHTML = `<div class="inc-head"><b>${esc(title)}</b><span class="mono">${e.runs} shopper run${e.runs === 1 ? "" : "s"} · ${esc(e.site)}</span></div>
    <p>${esc(e.message)} No score is given, because a site that could not be measured is not “transparent”.</p>
    ${e.screenshots.length ? `<div class="thumbs">${e.screenshots.map((s) => `<button class="thumb" type="button" data-src="/files/${esc(s)}" data-cap="The last page a shopper saw"><img src="/files/${esc(s)}" alt="The page that stopped the shopper" loading="lazy"><figcaption>What the shopper saw</figcaption></button>`).join("")}</div>` : ""}
    <p class="eyebrow">What you can do</p><ul>${(NEXT[e.reason] || NEXT.other).map((t) => `<li>${esc(t)}</li>`).join("")}</ul>`;
  box.hidden = false;
  bindZoom(box);
}

function watch(id) {
  const es = new EventSource(`/api/audits/${id}/events`);
  const done = () => { es.close(); $("run").disabled = false; $("report").classList.remove("stale"); };
  es.onmessage = (m) => {
    const e = JSON.parse(m.data);
    if (e.type === "started") {
      $("progressTitle").textContent = `${e.total} agent runs on ${e.provider}`;
      if (e.jobs) buildAgents(e.jobs);
    }
    if (e.type === "step") agentStep(e.key, e.step);
    if (e.type === "pinned") {
      $("pinned").hidden = false;
      $("pinned").innerHTML = `Every shopper now checks the same product: <b>${esc(e.product.name)}</b>`;
    }
    if (e.type === "run") {
      $("barfill").style.width = `${Math.round((e.done / e.total) * 88) + 6}%`;
      $("progressCount").textContent = `${e.done} / ${e.total}`;
      agentDone(e);
    }
    if (e.type === "analysing") { $("progressTitle").textContent = "Analysing: classifier, price model, statistics"; $("barfill").style.width = "94%"; }
    if (e.type === "policy") $("progressTitle").textContent = "Reading the site's written policies";
    if (e.type === "inconclusive") showInconclusive(e);
    if (e.type === "error") {
      if ($("inconclusive").hidden) $("progressTitle").textContent = "Error: " + e.message;
      done();
    }
    if (e.type === "complete") {
      $("barfill").style.width = "100%";
      $("progressTitle").textContent = "Audit complete";
      done();
      show(e.audit_id, true).then(() => setTimeout(() => ($("progress").hidden = true), 2500));
      loadBoard();
    }
  };
  es.onerror = done;
}

/* ---------- report ---------- */
/* ---------- 3D score ring (falls back to the plain number when WebGL is unavailable) ---------- */
let ring = null, ringTried = false;
const STATUS_VAR = { "var(--good)": "--good", "var(--warn)": "--warn", "var(--serious)": "--serious", "var(--crit)": "--crit" };
function setRing(score, st) {
  if (!ringTried) {
    ringTried = true;
    try { ring = Scene3D.webglOK() ? Scene3D.ScoreRing($("ringCanvas")) : null; } catch (e) { ring = null; }
    if (!ring) document.body.classList.add("no-webgl");
  }
  if (ring) ring.setScore(score, STATUS_VAR[st.c] || "--accent", true);
}

async function show(id, scroll = false) {
  const r = await api(`/api/audits/${id}`);
  S.report = r;
  S.sev = "all";
  $("report").hidden = false;

  const st = scoreStatus(r.trust_score);
  $("eyebrow").textContent = `Audit · ${r.provider} · ${new Date(r.created_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}`;
  $("siteName").textContent = r.site;
  const wf = r.waterfalls[0];
  const drip = wf && wf.drip_pct > 3 ? ` The advertised ${money(wf.advertised, wf.currency)} becomes ${money(wf.final, wf.currency)} at the payment screen (${pct(wf.drip_pct, 0)}).` : "";
  $("verdict").textContent = ({ Transparent: "Little to flag.", Mixed: "Several practices a shopper should know about.", Concerning: "Multiple practices work against the shopper.", Poor: "This site works against the shopper." })[st.word] + drip;
  $("runsNote").textContent = `${r.runs_ok} of ${r.runs_total} agent runs completed` + (r.notes.length ? ` · ${r.notes.join(" ")}` : "");
  $("scoreNum").textContent = Math.round(r.trust_score);
  setRing(r.trust_score, st);
  const pill = $("scoreWord");
  pill.innerHTML = `<span style="color:${st.c};display:inline-flex">${st.icon}</span>${esc(st.word)}`;

  $("reportLink").href = `/report?audit=${encodeURIComponent(r.audit_id)}`;
  drawProduct(r);
  drawKpis(r);
  drawEvidence(r);
  Assistant.setReport(r);
  drawCharts();
  drawFindings();
  drawBreakdown(r);
  if (scroll) $("report").scrollIntoView({ behavior: "smooth", block: "start" });
}

function drawKpis(r) {
  const byType = (t) => r.findings.filter((f) => f.type === t);
  const wf = r.waterfalls[0];
  const tiles = [];

  // price growth
  const d = wf ? wf.drip_pct : 0;
  tiles.push({ label: "Price growth to checkout", value: d > 3 ? pct(d, 0) : "None", clear: d <= 3, c: d >= 25 ? "--crit" : d >= 10 ? "--serious" : d > 3 ? "--warn" : "--good",
    sub: wf ? `${money(wf.advertised, wf.currency)} advertised, ${money(wf.final, wf.currency)} payable` : "No purchase flow completed" });

  // personalised pricing
  const eff = ((r.personalization.advertised || {}).effects || []).filter((e) => e.significant);
  const noise = (r.personalization.advertised || {}).noise_pct;
  const top = eff.slice().sort((a, b) => Math.abs(b.effect_pct) - Math.abs(a.effect_pct))[0];
  tiles.push(eff.length
    ? { label: "Personalised pricing", value: pct(top.effect_pct), c: Math.abs(top.effect_pct) >= 8 ? "--crit" : "--serious", sub: eff.length > 1 ? `${levelName(top.level)}: the largest of ${eff.length} flagged gaps (also ${eff.filter((e) => e !== top).map((e) => levelName(e.level)).join(", ")})` : `${levelName(top.level)} vs the baseline shopper` }
    : { label: "Personalised pricing", value: "None", clear: true, c: "--good", sub: noise != null ? `All gaps within ±${noise}% noise` : "Not enough runs to test" });

  // cancel effort
  const fr = r.friction || {};
  tiles.push(fr.cancel_clicks != null
    ? { label: "Effort to cancel", value: `${Math.round(fr.cancel_clicks)} clicks`, clear: fr.ratio < 3 && fr.cancel_clicks < 5, c: fr.ratio >= 5 ? "--crit" : fr.ratio >= 3 ? "--serious" : "--good", sub: `against ${Math.round(fr.signup_clicks)} to sign up` }
    : { label: "Effort to cancel", value: "n/a", clear: true, c: "--ink-3", sub: "No cancellation flow tested" });

  // dark patterns
  const pats = r.findings.filter((f) => f.type.startsWith("pattern:") || f.type === "basket_sneaking");
  tiles.push({ label: "Dark patterns", value: pats.length ? String(pats.length) : "None", clear: !pats.length, c: pats.length >= 4 ? "--crit" : pats.length ? "--serious" : "--good",
    sub: pats.length ? pats.slice(0, 2).map((f) => f.title.split(" (")[0].split(":")[0]).join(", ") + (pats.length > 2 ? ` +${pats.length - 2}` : "") : "Across 8 tactics checked" });

  // policy
  const pol = byType("policy");
  const skipped = r.notes.some((n) => /Policy check/.test(n));
  tiles.push(pol.length
    ? { label: "Policy contradictions", value: String(pol.length), c: "--crit", sub: "Promises the measurements disprove" }
    : { label: "Policy contradictions", value: skipped ? "Not checked" : "None", clear: true, c: skipped ? "--ink-3" : "--good", sub: skipped ? "Gemini was busy or out of quota" : "Written promises match what we saw" });

  $("kpis").innerHTML = tiles.map((t) => `
    <div class="kpi ${t.clear ? "clear" : ""}">
      <div class="k-label"><i class="sev-mark" style="--c:var(${t.c})"></i>${esc(t.label)}</div>
      <div class="k-value">${esc(t.value)}</div>
      <div class="k-sub">${esc(t.sub)}</div>
    </div>`).join("");
}

/* ---------- charts ---------- */
function niceStep(range, n) {
  const raw = range / n, mag = 10 ** Math.floor(Math.log10(raw)), norm = raw / mag;
  return (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
}
const lum = (hex) => {
  const m = hex.replace("#", ""), v = [0, 2, 4].map((i) => parseInt(m.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
};
const onFill = (varName) => (lum(cssVar(varName)) > 0.22 ? "#0b0b0b" : "#ffffff");

function groups(w) {
  const sum = (...k) => w.items.filter((i) => k.includes(i.kind)).reduce((a, i) => a + i.amount, 0);
  return [
    { key: "price", name: "Price", c: "--s1", v: sum("base") || w.advertised || 0 },
    { key: "fees", name: "Fees & taxes", c: "--s2", v: sum("fee", "tax") },
    { key: "addons", name: "Add-ons", c: "--s3", v: sum("addon") },
  ];
}

function drawCharts() {
  drawWaterfall();
  drawEffects();
}

function drawWaterfall() {
  const r = S.report, host = $("wfBody"), legend = $("wfLegend");
  if (!r) return;
  const rows = r.waterfalls;
  legend.innerHTML = `<span class="key"><i class="sw" style="--c:var(--s1)"></i>Price</span><span class="key"><i class="sw" style="--c:var(--s2)"></i>Fees &amp; taxes</span><span class="key"><i class="sw" style="--c:var(--s3)"></i>Add-ons</span><span class="key"><i class="dia"></i>Advertised price</span>`;
  if (!rows.length) { host.innerHTML = '<p class="muted">No completed purchase runs to chart.</p>'; return; }
  const cur = rows[0].currency;

  if (S.wfView === "table") {
    host.innerHTML = `<div class="tablewrap"><table class="data"><thead><tr><th>Persona</th><th class="r">Price</th><th class="r">Fees &amp; taxes</th><th class="r">Add-ons</th><th class="r">Advertised</th><th class="r">Payable</th><th class="r">Hidden</th></tr></thead><tbody>` +
      rows.map((w) => { const g = groups(w); return `<tr><td>${esc(personaName(w))} <span class="muted">${esc(w.persona)}</span></td><td class="r">${money(g[0].v, cur)}</td><td class="r">${money(g[1].v, cur)}</td><td class="r">${money(g[2].v, cur)}</td><td class="r">${money(w.advertised, cur)}</td><td class="r">${money(w.final, cur)}</td><td class="r">${pct(w.drip_pct, 0)}</td></tr>`; }).join("") + "</tbody></table></div>";
    return;
  }

  const W = Math.max(300, host.clientWidth || 600);
  const narrow = W < 460;
  const left = narrow ? 84 : Math.min(158, Math.max(104, W * 0.25)), right = narrow ? 84 : 112;
  const rowH = 50, barH = 22, top = 20, axisH = 28;
  const plotW = W - left - right, H = top + rows.length * rowH + axisH;
  const maxV = Math.max(...rows.map((w) => Math.max(w.final || 0, w.advertised || 0)));
  const step = niceStep(maxV, 4);
  const domain = Math.ceil(maxV / step) * step;
  const x = (v) => left + (v / domain) * plotW;

  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Stacked bars: advertised price versus payable price for each persona" });
  for (let v = 0; v <= domain + 1e-6; v += step) {
    svg.append(sv("line", { x1: x(v), x2: x(v), y1: top - 8, y2: top + rows.length * rowH, class: v === 0 ? "axisline" : "gridline" }));
    svg.append(sv("text", { x: x(v), y: top + rows.length * rowH + 18, "text-anchor": "middle", "font-size": 11.5, style: "fill:var(--ink-3)" }, document.createTextNode(axisMoney(v, cur))));
  }

  rows.forEach((w, i) => {
    const gs = groups(w), g = sv("g", { class: "row", transform: `translate(0 ${top + i * rowH})`, tabindex: 0, role: "img", "aria-label": `${personaName(w)}: advertised ${money(w.advertised, cur)}, payable ${money(w.final, cur)}` });
    g.append(sv("rect", { class: "hit", x: 0, y: 0, width: W, height: rowH }));
    const y = (rowH - barH) / 2;
    const l1 = sv("text", { x: left - 12, y: y + 10, "text-anchor": "end", "font-size": 13, "font-weight": 560, style: "fill:var(--ink)" }, document.createTextNode(personaName(w)));
    g.append(l1);
    if (!narrow) g.append(sv("text", { x: left - 12, y: y + 24, "text-anchor": "end", "font-size": 11, style: "fill:var(--ink-3)" }, document.createTextNode(w.persona)));

    let cum = 0;
    const live = gs.filter((s) => s.v > 0);
    live.forEach((s, k) => {
      const x0 = x(cum) + (k ? 1 : 0), x1 = x(cum + s.v) - (k < live.length - 1 ? 1 : 0), wd = Math.max(0, x1 - x0);
      cum += s.v;
      const last = k === live.length - 1, rr = Math.min(4, wd / 2);
      const d = last
        ? `M${x0},${y} H${x0 + wd - rr} a${rr},${rr} 0 0 1 ${rr},${rr} V${y + barH - rr} a${rr},${rr} 0 0 1 ${-rr},${rr} H${x0} Z`
        : `M${x0},${y} h${wd} v${barH} h${-wd} Z`;
      g.append(sv("path", { d, class: "seg-rect", style: `fill:var(${s.c})` }));
      if (i === 0 && wd >= approxW(s.name, 11.5) + 18) {
        g.append(sv("text", { x: x0 + wd / 2, y: y + barH / 2 + 4, "text-anchor": "middle", "font-size": 11.5, "font-weight": 600, style: `fill:${onFill(s.c)}` }, document.createTextNode(s.name)));
      }
    });

    // advertised-price marker
    if (w.advertised) {
      const ax = x(w.advertised);
      g.append(sv("path", { d: `M${ax},${y - 5} V${y + barH + 5}`, stroke: "var(--ink)", "stroke-width": 2, "stroke-linecap": "round" }));
      g.append(sv("path", { d: `M${ax},${y - 10} l4,4 l-4,4 l-4,-4 Z`, style: "fill:var(--ink)" }));
    }
    // tip label (value at the bar end)
    const tl = sv("text", { x: x(w.final) + 10, y: y + barH / 2 + 4.5, "font-size": 13, "font-weight": 640, style: "fill:var(--ink)" });
    tl.append(document.createTextNode(money(w.final, cur)));
    if (w.drip_pct > 0.5) tl.append(sv("tspan", { dx: 6, "font-weight": 520, "font-size": 12, style: "fill:var(--ink-2)" }, document.createTextNode(pct(w.drip_pct, 0))));
    g.append(tl);

    bindTip(g, () => ({
      title: `${personaName(w)} · ${w.persona}`,
      rows: w.items.map((it) => ({ c: `var(${it.kind === "addon" ? "--s3" : it.kind === "fee" || it.kind === "tax" ? "--s2" : "--s1"})`, name: it.label + (it.preselected ? " (pre-ticked)" : ""), value: money(it.amount, cur) })),
      foot: `Advertised ${money(w.advertised, cur)} · payable ${money(w.final, cur)} (${pct(w.drip_pct, 0)})`,
    }));
    svg.append(g);
  });
  host.replaceChildren(svg);
}

function drawEffects() {
  const r = S.report, host = $("effBody"), legend = $("effLegend"), p = (r && r.personalization.advertised) || {};
  if (!r) return;
  const fx = (p.effects || []).slice().sort((a, b) => Math.abs(b.effect_pct) - Math.abs(a.effect_pct));
  $("effSub").textContent = p.baseline ? `Price difference against the baseline shopper (${COUNTRY[p.baseline.country] || p.baseline.country}, ${p.baseline.device}, ${p.baseline.user_state}), with 95% confidence interval.` : "Price difference against the baseline shopper.";
  legend.innerHTML = `<span class="key"><i class="sw" style="--c:var(--crit);border-radius:50%"></i>Beats the site's noise</span><span class="key"><i class="sw" style="--c:var(--ink-3);border-radius:50%"></i>Within noise</span><span class="key"><i class="sw" style="--c:var(--accent);opacity:.35"></i>Normal price noise (±1 sd)</span>`;
  $("effNote").textContent = p.note || "";
  if (!fx.length) { host.innerHTML = `<p class="muted">${esc(p.note || "Not enough data to test.")}</p>`; return; }

  if (S.effView === "table") {
    host.innerHTML = `<div class="tablewrap"><table class="data"><thead><tr><th>Persona</th><th class="r">Effect</th><th class="r">95% CI</th><th class="r">p (adj.)</th><th>Verdict</th></tr></thead><tbody>` +
      fx.map((e) => `<tr><td>${esc(levelName(e.level))} <span class="muted">vs ${esc(levelName(e.vs))}</span></td><td class="r">${pct(e.effect_pct)}</td><td class="r">${pct(e.ci_low_pct)} to ${pct(e.ci_high_pct)}</td><td class="r">${e.p_adj < 0.001 ? "&lt; 0.001" : e.p_adj}</td><td>${e.significant ? "Flagged" : "Within noise"}</td></tr>`).join("") + "</tbody></table></div>";
    return;
  }

  const W = Math.max(300, host.clientWidth || 520), narrow = W < 460;
  const left = narrow ? 92 : Math.min(160, Math.max(112, W * 0.27)), right = narrow ? 70 : 96;
  const rowH = 46, top = 26, axisH = 28, H = top + fx.length * rowH + axisH, plotW = W - left - right;
  const noise = p.noise_pct || 0;
  let lo = Math.min(-3, ...fx.map((e) => e.ci_low_pct)) - 0.5, hi = Math.max(3, ...fx.map((e) => e.ci_high_pct)) + 0.5;
  const step = niceStep(hi - lo, 5);
  lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
  const x = (v) => left + ((v - lo) / (hi - lo)) * plotW;

  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Price effect per persona with confidence intervals" });
  svg.append(sv("rect", { x: x(-noise), y: top - 8, width: Math.max(2, x(noise) - x(-noise)), height: fx.length * rowH + 8, style: "fill:var(--accent);opacity:.14" }));
  if (!narrow) svg.append(sv("text", { x: x(0), y: top - 13, "text-anchor": "middle", "font-size": 11, style: "fill:var(--ink-3)" }, document.createTextNode(`noise ±${noise}%`)));
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-6; v += step) {
    const vv = +v.toFixed(6);
    svg.append(sv("line", { x1: x(vv), x2: x(vv), y1: top - 8, y2: top + fx.length * rowH, class: vv === 0 ? "axisline" : "gridline", style: vv === 0 ? "stroke:var(--ink-3)" : null }));
    svg.append(sv("text", { x: x(vv), y: top + fx.length * rowH + 18, "text-anchor": "middle", "font-size": 11.5, style: "fill:var(--ink-3)" }, document.createTextNode(vv === 0 ? "0%" : `${vv > 0 ? "+" : "−"}${Math.abs(vv)}%`)));
  }
  fx.forEach((e, i) => {
    const color = e.significant ? "var(--crit)" : "var(--ink-3)";
    const g = sv("g", { class: "row", transform: `translate(0 ${top + i * rowH})`, tabindex: 0, role: "img", "aria-label": `${levelName(e.level)} versus ${levelName(e.vs)}: ${pct(e.effect_pct)}, ${e.significant ? "flagged" : "within noise"}` });
    g.append(sv("rect", { class: "hit", x: 0, y: 0, width: W, height: rowH }));
    const cy = rowH / 2;
    g.append(sv("text", { x: left - 12, y: cy - 2, "text-anchor": "end", "font-size": 13, "font-weight": 560, style: "fill:var(--ink)" }, document.createTextNode(levelName(e.level))));
    if (!narrow) g.append(sv("text", { x: left - 12, y: cy + 13, "text-anchor": "end", "font-size": 11, style: "fill:var(--ink-3)" }, document.createTextNode(`vs ${levelName(e.vs)}`)));
    g.append(sv("path", { d: `M${x(e.ci_low_pct)},${cy} H${x(e.ci_high_pct)}`, stroke: color, "stroke-width": 2, "stroke-linecap": "round" }));
    for (const cx of [e.ci_low_pct, e.ci_high_pct]) g.append(sv("path", { d: `M${x(cx)},${cy - 5} V${cy + 5}`, stroke: color, "stroke-width": 2, "stroke-linecap": "round" }));
    g.append(sv("circle", { cx: x(e.effect_pct), cy, r: 5.5, style: `fill:${color};stroke:var(--surface);stroke-width:2` }));
    const vt = sv("text", { x: left + plotW + 14, y: cy - 1, "font-size": 13, "font-weight": 640, style: "fill:var(--ink)" }, document.createTextNode(pct(e.effect_pct)));
    g.append(vt);
    if (!narrow) g.append(sv("text", { x: left + plotW + 14, y: cy + 13, "font-size": 11, style: "fill:var(--ink-3)" }, document.createTextNode(e.significant ? "flagged" : "within noise")));
    bindTip(g, () => ({
      title: `${levelName(e.level)} vs ${levelName(e.vs)}`,
      rows: [{ c: color, name: "Price effect", value: pct(e.effect_pct) }, { name: "95% interval", value: `${pct(e.ci_low_pct)} to ${pct(e.ci_high_pct)}` }, { name: "p (adjusted)", value: e.p_adj < 0.001 ? "< 0.001" : String(e.p_adj) }],
      foot: e.significant ? "Larger than the site's own price noise." : "Not distinguishable from random price noise.",
    }));
    svg.append(g);
  });
  host.replaceChildren(svg);
}

for (const [name, key, fn] of [["wfview", "wfView", drawWaterfall], ["effview", "effView", drawEffects]]) {
  document.querySelectorAll(`input[name="${name}"]`).forEach((i) => (i.onchange = () => { S[key] = i.value; fn(); }));
}
let rz;
new ResizeObserver(() => { clearTimeout(rz); rz = setTimeout(() => { if (S.report) drawCharts(); }, 120); }).observe(document.body);

/* ---------- findings ---------- */
function drawFindings() {
  const r = S.report, all = r.findings;
  const cnt = (s) => all.filter((f) => f.severity === s).length;
  $("findSub").textContent = all.length ? `${all.length} found, ordered by effect on the score. Open one for the evidence.` : "Nothing to flag.";
  const opts = [["all", `All ${all.length}`], ["high", `High ${cnt("high")}`], ["medium", `Medium ${cnt("medium")}`], ["low", `Low ${cnt("low")}`]];
  const box = $("sevFilter");
  box.innerHTML = opts.map(([v, t]) => `<input type="radio" name="sev" id="sev-${v}" value="${v}" ${S.sev === v ? "checked" : ""}><label for="sev-${v}">${t}</label>`).join("");
  box.querySelectorAll("input").forEach((i) => (i.onchange = () => { S.sev = i.value; renderFindingList(); }));
  renderFindingList();
}
function renderFindingList() {
  const list = S.report.findings.filter((f) => S.sev === "all" || f.severity === S.sev);
  if (!list.length) {
    $("findings").innerHTML = `<div class="empty">${ICON.check}<span>${S.report.findings.length ? "No findings at this severity." : "Nothing to flag. This site passed every check we run."}</span></div>`;
    return;
  }
  $("findings").innerHTML = list.map((f, i) => `
    <details class="finding" ${i < 2 && S.sev === "all" ? "open" : ""}>
      <summary><span class="sev sev-${f.severity}"><i></i>${esc(f.severity)}</span><span class="f-title">${esc(f.title)}</span><span class="f-pts">−${f.penalty.toFixed(1)}</span><span class="chev" aria-hidden="true"></span></summary>
      <div class="f-body">
        <div class="tags">${f.ccpa ? `<span class="tag">CCPA category: ${esc(f.ccpa)}</span>` : ""}${f.type.startsWith("pattern:") ? `<span class="tag">Text classifier</span>` : f.type === "policy" ? `<span class="tag">Fetch + Gemini</span>` : `<span class="tag">Measured</span>`}</div>
        <p>${esc(f.detail)}</p>
        ${f.evidence.some((e) => e.screenshot) ? `<div class="thumbs">${f.evidence.filter((e) => e.screenshot).map((e) => `<button class="thumb" type="button" data-src="/files/${esc(e.screenshot)}" data-cap="${esc(e.text)}"><img loading="lazy" alt="Screenshot: ${esc(e.step_name)}" src="/files/${esc(e.screenshot)}"><figcaption>${esc(e.step_name.replace(/_/g, " "))}${e.text ? " · " + esc(e.text).slice(0, 56) : ""}</figcaption></button>`).join("")}</div>` : ""}
      </div>
    </details>`).join("");
  bindZoom(document);
}

function bindZoom(scope) {
  scope.querySelectorAll("[data-src]").forEach((b) => (b.onclick = () => {
    $("modalBody").innerHTML = `<img src="${esc(b.dataset.src)}" alt="Evidence screenshot"><p class="muted small" style="margin-top:10px">${esc(b.dataset.cap)}</p>`;
    $("modal").showModal();
  }));
}

/* ---------- which product the shoppers priced, and whether it was the same one ---------- */
function drawProduct(r) {
  const ps = r.products || [], used = ps.find((p) => p.used) || ps[0], el = $("productLine");
  if (!used) { el.hidden = true; return; }
  const name = used.url ? `<a href="${esc(used.url)}" target="_blank" rel="noopener noreferrer">${esc(used.name || used.url)}</a>` : esc(used.name);
  const others = ps.filter((p) => p !== used);
  el.className = "product-line" + (others.length ? " mixed" : "");
  el.innerHTML = `<span class="pl-k">Product checked</span> ${name} <span class="pl-v">${others.length
    ? `${used.runs} runs compared · ${others.reduce((n, p) => n + p.runs, 0)} run(s) on a different product left out`
    : `same product in all ${used.runs} measured runs`}</span>`;
  el.hidden = false;
}

/* ---------- evidence strip: the first screen, what got added on the way, the payment screen ---------- */
function drawEvidence(r) {
  // One real run (the baseline shopper's median run): its screenshots, its line items and its prices.
  const wf = r.waterfalls[0], run = wf && wf.run;
  if (!run || !run.first_screenshot || !wf.screenshot || !run.advertised || !run.final || run.final <= run.advertised * 1.03) { $("evidence").hidden = true; return; }
  const cur = wf.currency;
  const sneaking = r.findings.some((f) => f.type === "basket_sneaking");
  const kind = { base: "Item", fee: "Fee", tax: "Tax", addon: "Add-on", discount: "Discount", other: "Other" };
  const rows = wf.items.map((it) => {
    const pre = it.preselected || (it.kind === "addon" && sneaking);
    return `<li class="k-${esc(it.kind)}"><span><i class="ev-k">${esc(kind[it.kind] || "Line")}</i>${esc(it.label)}${pre ? ' <em class="ev-pre">pre-ticked</em>' : ""}</span><b>${it.kind === "base" ? "" : "+"}${money(it.amount, cur)}</b></li>`;
  }).join("");
  const fig = (src, tag, price, cls) => `<figure class="ev-shot ${cls}"><button type="button" data-src="/files/${esc(src)}" data-cap="${esc(`${tag}: ${money(price, cur)}`)}" aria-label="Enlarge the ${esc(tag.toLowerCase())} screenshot"><img src="/files/${esc(src)}" alt="${esc(tag)} screenshot" loading="lazy"></button><figcaption><span>${esc(tag)}</span><b>${money(price, cur)}</b></figcaption></figure>`;
  const growth = (run.final / run.advertised - 1) * 100;
  $("evStrip").innerHTML = fig(run.first_screenshot, "First screen", run.advertised, "before")
    + `<div class="ev-receipt" aria-label="What was added on the way"><p class="eyebrow">Added on the way</p><ol>${rows}</ol><div class="ev-total"><span>Payable</span><b>${money(run.final, cur)}</b></div><p class="ev-delta">${pct(growth, 0)} over the advertised price</p></div>`
    + fig(wf.screenshot, run.stopped_at === "price_screen" ? "Checkout summary" : "Payment screen", run.final, "after");
  const prod = (r.products || []).find((p) => p.used);
  $("evSub").textContent = `${prod ? prod.name + " · " : ""}One ${personaName(wf).toLowerCase()} shopper run: the median of ${wf.n}. Click a screenshot to enlarge it.`;
  $("evStamp").textContent = `run ${run.run_id} · ${new Date(r.created_at).toISOString().slice(0, 16).replace("T", " ")} UTC`;
  $("evidence").hidden = false;
  bindZoom($("evStrip"));
}

function drawBreakdown(r) {
  const max = Math.max(1, ...r.score_breakdown.map((b) => b.penalty));
  $("breakdown").innerHTML = r.score_breakdown.map((b) => `<div class="brow"><div class="bt"><span title="${esc(b.title)}">${esc(b.title)}</span><span>−${b.penalty}</span></div><div class="meter"><i style="width:${(b.penalty / max) * 100}%"></i></div></div>`).join("") || '<p class="muted small">No deductions.</p>';
}

/* ---------- complaint ---------- */
$("complaintBtn").onclick = () => {
  if (!S.report) return;
  $("modalBody").innerHTML = `<h2 style="font-size:18px;margin-bottom:4px">Draft a complaint</h2><p class="muted small" style="margin-bottom:14px">For India's National Consumer Helpline. It uses only the findings of this audit. Attach the screenshots.</p>
    <div class="field" style="margin-bottom:12px"><label for="cname">Your name</label><input class="input" id="cname" type="text" maxlength="80" value="${esc(PD.user.complaint_name || (PD.user.is_guest ? "" : PD.user.name))}" placeholder="Optional"></div>
    <button class="btn primary" id="cgo" type="button">Generate draft</button><div id="cout"></div>`;
  $("modal").showModal();
  $("cgo").onclick = async () => {
    $("cgo").disabled = true; $("cgo").textContent = "Writing…";
    try {
      const out = await post(`/api/audits/${S.report.audit_id}/complaint`, { name: $("cname").value.trim() || "[Your name]" });
      $("cout").innerHTML = `<p class="muted small" style="margin:14px 0 8px">Written by ${esc(out.method)}</p><textarea id="ctext" aria-label="Complaint text"></textarea><div class="modal-actions"><button class="btn primary" id="ccopy" type="button">Copy text</button><span id="cmsg" class="muted small"></span></div>`;
      $("ctext").value = out.text;
      $("ccopy").onclick = async () => {
        try { await navigator.clipboard.writeText($("ctext").value); $("cmsg").textContent = "Copied."; } catch (e) { $("ctext").select(); $("cmsg").textContent = "Press Ctrl+C to copy."; }
      };
    } catch (e) { $("cout").innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
    $("cgo").disabled = false; $("cgo").textContent = "Generate again";
  };
};

/* ---------- leaderboard + quality ---------- */
async function loadBoard() {
  const a = await api("/api/audits");
  const seen = new Map();
  a.forEach((x) => { const k = x.site + "|" + x.provider; if (!seen.has(k)) seen.set(k, x); });
  const rows = [...seen.values()].sort((x, y) => y.trust_score - x.trust_score);
  $("board").innerHTML = rows.map((x) => { const st = scoreStatus(x.trust_score); return `<button class="board-row" type="button" data-id="${esc(x.audit_id)}"><span class="b-site">${esc(x.site)}<small>${esc(x.provider)}</small></span><span class="meter" style="--m:${st.c}"><i style="width:${Math.max(3, x.trust_score)}%"></i></span><span class="b-score">${Math.round(x.trust_score)}</span></button>`; }).join("") || '<p class="muted small">No audits yet. Run one above.</p>';
  document.querySelectorAll(".board-row").forEach((el) => (el.onclick = () => show(el.dataset.id, true)));
  return a;
}
async function loadQuality() {
  const [clf, ev] = await Promise.all([api("/api/classifier").catch(() => ({})), api("/api/eval").catch(() => ({}))]);
  const tiles = [];
  const m = clf.tfidf_logreg;
  if (m) tiles.push([m.macro_f1.toFixed(2), `Text classifier macro-F1 (n=${m.n})`]);
  if (clf.llm_zero_shot) tiles.push([clf.llm_zero_shot.macro_f1.toFixed(2), "Gemini zero-shot, same data"]);
  if (ev.scores) tiles.push([ev.scores.overall.f1.toFixed(2), "End-to-end F1 vs known ground truth"]);
  if (ev.scores) tiles.push([`${Math.round(ev.scores.personalization.recall * 100)}%`, "Planted price biases recovered"]);
  $("quality").innerHTML = tiles.length
    ? `<div class="q-grid">${tiles.map(([v, l]) => `<div class="q-tile"><span class="k-value">${esc(v)}</span><span class="k-label">${esc(l)}</span></div>`).join("")}</div><p class="muted small q-note">The ground truth is a demo shop with known planted tricks, so these numbers show the pipeline recovers what is there. They do not prove real sites behave the same.</p>`
    : '<p class="muted small">Run <span class="mono">python -m app.ml.pattern_clf</span> and <span class="mono">python -m eval.evaluate</span> to fill this in.</p>';
}

/* ---------- boot: open on the most recent audit so the page is never an empty shell ---------- */
(async function boot() {
  const user = await bootSignedIn("app");
  if (!user) return;
  // The user's saved defaults from onboarding / profile.
  const saved = document.querySelector(`input[name="provider"][value="${user.default_provider}"]`);
  if (saved) saved.checked = true;
  $("quick").checked = !!user.default_quick;
  loadHealth(); loadSites(); loadQuality();
  const audits = await loadBoard().catch(() => []);
  const want = new URLSearchParams(location.search).get("audit");  // deep link from the profile's history
  const target = want && audits.some((a) => a.audit_id === want) ? want : audits[0] && audits[0].audit_id;
  if (target) show(target, !!want).catch(() => {});
  setInterval(loadHealth, 15000);
})();
