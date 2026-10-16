"use strict";
/* Printable findings report for one audit. Built from the same report JSON as the console, plus the
   dossier endpoint (reference number, method, SHA-256 fingerprints). Print or "Save as PDF" from the browser. */

const SYM = { INR: "₹", USD: "$", EUR: "€", GBP: "£", JPY: "¥", "": "₹" };
const money = (n, cur) => (n == null ? "n/a" : `${SYM[cur] ?? cur + " "}${Math.round(n).toLocaleString("en-IN")}`);
const pct = (n, d = 1) => `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toFixed(d)}%`;
const utc = (iso, secs = false) => (iso ? new Date(iso).toISOString().slice(0, secs ? 19 : 16).replace("T", " ") + " UTC" : "n/a");
const day = (iso) => new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
const LEVEL = { iphone: "iPhone users", android: "Android users", returning: "Returning visitors", US: "United States", GB: "United Kingdom", DE: "Germany", JP: "Japan", IN: "India" };

function verdict(s) {
  if (s >= 75) return { word: "Transparent", cls: "good", line: "Little to flag." };
  if (s >= 45) return { word: "Mixed", cls: "warn", line: "Several practices a shopper should know about." };
  if (s >= 25) return { word: "Concerning", cls: "serious", line: "Multiple practices work against the shopper." };
  return { word: "Opaque", cls: "crit", line: "This site works against the shopper." };
}

const ID = new URLSearchParams(location.search).get("audit");
const R = { report: null, dossier: null, exhibits: [] };

/* Number every cited screenshot once (E-1, E-2, ...) so findings can refer to exhibits. */
function exhibitsFor(r) {
  const list = [], index = new Map();
  for (const f of r.findings) {
    for (const e of f.evidence) {
      if (!e.screenshot || index.has(e.screenshot)) continue;
      index.set(e.screenshot, list.length + 1);
      list.push({ n: list.length + 1, src: e.screenshot, step: e.step_name.replace(/_/g, " "), text: e.text, finding: f.title });
    }
  }
  return { list, index };
}

function figures(r) {
  const wf = r.waterfalls[0];
  const eff = ((r.personalization.advertised || {}).effects || []).filter((e) => e.significant);
  const top = eff.sort((a, b) => Math.abs(b.effect_pct) - Math.abs(a.effect_pct))[0];
  const fr = r.friction || {};
  const pats = r.findings.filter((f) => f.type.startsWith("pattern:"));
  const pol = r.findings.filter((f) => f.type === "policy");
  return [
    ["Price growth to checkout", wf && wf.drip_pct > 3 ? pct(wf.drip_pct, 0) : "None", wf ? `${money(wf.advertised, wf.currency)} advertised, ${money(wf.final, wf.currency)} payable` : ""],
    ["Personalised pricing", top ? pct(top.effect_pct) : "None found", top ? `${LEVEL[top.level] || top.level} vs ${top.vs}, beyond ${r.personalization.advertised.noise_pct}% noise` : "Within measured noise"],
    ["Effort to cancel", fr.cancel_clicks != null ? `${Math.round(fr.cancel_clicks)} clicks` : "n/a", fr.signup_clicks != null ? `against ${Math.round(fr.signup_clicks)} to sign up` : "Not tested"],
    ["Dark patterns in copy", String(pats.length || "None"), pats.length ? "Detected by the text classifier" : ""],
    ["Policy contradictions", String(pol.length || "None"), pol.length ? "Written promises the runs disprove" : ""],
  ];
}

function render() {
  const r = R.report, d = R.dossier, v = verdict(r.trust_score), m = d.method;
  const { list, index } = exhibitsFor(r);
  R.exhibits = list;
  const wf = r.waterfalls[0];
  const run = wf && wf.run;
  const showA = run && run.first_screenshot && wf.screenshot && run.advertised && run.final > run.advertised * 1.03;
  const prods = r.products || [], prod = prods.find((p) => p.used) || prods[0];
  const otherRuns = prods.filter((p) => p !== prod).reduce((n, p) => n + p.runs, 0);
  const prodLine = prod ? `The product priced was <b>${esc(prod.name)}</b>${otherRuns
    ? `; ${otherRuns} run(s) reached a different product and were left out of the price comparisons`
    : `, the same product in all ${prod.runs} measured runs`}.` : "";
  const personas = m.personas.length;
  const runs = Object.values(m.runs_by_flow).reduce((a, b) => a + b, 0);
  document.title = `${d.reference} · ${r.site} · Pardarshi`;

  const exhibitA = showA ? `
    <section class="doc-sec">
      <h2><span class="sec-n">2</span>Exhibit A: one order, from first screen to payment</h2>
      <div class="exA">
        <figure><img src="/files/${esc(run.first_screenshot)}" alt="First screen"><figcaption><span>First screen</span><b>${money(run.advertised, wf.currency)}</b></figcaption></figure>
        <table class="ledger"><thead><tr><th>Line</th><th class="r">Amount</th></tr></thead><tbody>
          ${wf.items.map((it) => `<tr><td>${esc(it.label)} <small>${esc(it.kind)}</small></td><td class="r">${it.kind === "base" ? "" : "+"}${money(it.amount, wf.currency)}</td></tr>`).join("")}
          </tbody><tfoot><tr><td>Payable</td><td class="r">${money(run.final, wf.currency)}</td></tr><tr class="delta"><td>Over advertised</td><td class="r">${pct((run.final / run.advertised - 1) * 100, 0)}</td></tr></tfoot></table>
        <figure class="after"><img src="/files/${esc(wf.screenshot)}" alt="${run.stopped_at === "price_screen" ? "Checkout summary" : "Payment screen"}"><figcaption><span>${run.stopped_at === "price_screen" ? "Checkout summary" : "Payment screen"}</span><b>${money(run.final, wf.currency)}</b></figcaption></figure>
      </div>
      <p class="fine">${prod ? `Product: ${esc(prod.name)}. ` : ""}Run <span class="mono">${esc(run.run_id)}</span>, shopper profile ${esc(wf.persona)}: the median of ${wf.n} runs for that profile. ${run.stopped_at === "price_screen" ? "The site asked for a login or personal details next, so the agent stopped at this checkout summary" : "The agent stopped at the payment screen"}; nothing was bought.</p>
    </section>` : "";

  $("sheet").innerHTML = `
    <header class="mast">
      <div class="mast-brand">
        <svg width="32" height="32" viewBox="0 0 64 64" aria-hidden="true"><rect class="mk-tile" width="64" height="64" rx="16" fill="#27358f"/><circle cx="34" cy="26" r="12" fill="#fff" fill-opacity=".10"/><path d="M28.5 22.5 H39.5" stroke="#fff" stroke-opacity=".62" stroke-width="2.4" stroke-linecap="round"/><path d="M28.5 28.5 H36" stroke="#f0b429" stroke-width="3.2" stroke-linecap="round"/><circle cx="34" cy="26" r="12" fill="none" stroke="#fff" stroke-width="5"/><path d="M22 26 V50" stroke="#fff" stroke-width="5.5" stroke-linecap="round"/></svg>
        <div><b>Pardarshi</b> <span class="deva" lang="hi">पारदर्शी</span><small>Independent checkout audit</small></div>
      </div>
      <dl class="mast-meta">
        <div><dt>Reference</dt><dd class="mono">${esc(d.reference)}</dd></div>
        <div><dt>Issued</dt><dd>${esc(day(r.created_at))}</dd></div>
        <div><dt>Audit ID</dt><dd class="mono">${esc(r.audit_id)}</dd></div>
      </dl>
    </header>

    <section class="title-block">
      <p class="kicker">Findings report</p>
      <h1>${esc(r.site)}</h1>
      <p class="lead">${esc(v.line)} ${wf && wf.drip_pct > 3 ? `The advertised ${money(wf.advertised, wf.currency)} became ${money(wf.final, wf.currency)} at the payment screen.` : ""}</p>
      <div class="stamp ${v.cls}" aria-label="Verdict: ${esc(v.word)}"><span>Verdict</span><b>${esc(v.word)}</b><i>${Math.round(r.trust_score)} / 100</i></div>
    </section>

    <section class="doc-sec">
      <h2><span class="sec-n">1</span>Summary</h2>
      <p>Between ${esc(utc(m.started_at, true))} and ${esc(utc(m.finished_at, true))}, Pardarshi sent automated shoppers through ${esc(r.site)} using the <b>${esc(m.provider)}</b> browser provider. ${personas} shopper profile${personas === 1 ? "" : "s"} each repeated the purchase ${m.repeats} time${m.repeats === 1 ? "" : "s"}, and the cancellation flow was walked separately: ${runs} sessions in all, ${r.runs_ok} completed. Every session stopped before payment. ${prodLine} ${r.findings.length} finding${r.findings.length === 1 ? "" : "s"} are listed in section 3.</p>
      <div class="figs">${figures(r).map(([k, val, sub]) => `<div><span>${esc(k)}</span><b>${esc(val)}</b><small>${esc(sub)}</small></div>`).join("")}</div>
    </section>

    ${exhibitA}

    <section class="doc-sec">
      <h2><span class="sec-n">3</span>Findings</h2>
      <table class="findings">
        <thead><tr><th>No.</th><th>Finding</th><th>Category (CCPA 2023)</th><th>Severity</th><th class="r">Points</th><th>Exhibits</th></tr></thead>
        <tbody>${r.findings.map((f, i) => {
          const ex = [...new Set(f.evidence.filter((e) => e.screenshot).map((e) => index.get(e.screenshot)))];
          return `<tr><td class="mono">F-${i + 1}</td><td><b>${esc(f.title)}</b><p>${esc(f.detail)}</p><small>${f.type.startsWith("pattern:") ? "Text classifier" : f.type === "policy" ? "Policy text vs measured behaviour" : "Measured"}</small></td><td>${esc(f.ccpa || "n/a")}</td><td><span class="sev ${esc(f.severity)}">${esc(f.severity)}</span></td><td class="r mono">−${f.penalty.toFixed(1)}</td><td class="mono">${ex.length ? ex.map((n) => `E-${n}`).join(", ") : "Statistics"}</td></tr>`;
        }).join("")}</tbody>
      </table>
      <p class="fine">Score = 100 × e<sup>−(total points)/70</sup>. Total points ${r.findings.reduce((a, f) => a + f.penalty, 0).toFixed(1)}; score ${Math.round(r.trust_score)} of 100.</p>
    </section>

    <section class="doc-sec">
      <h2><span class="sec-n">4</span>Method</h2>
      <dl class="method">
        <div><dt>Browser provider</dt><dd>${esc(m.provider)}</dd></div>
        ${prods.length ? `<div><dt>Product</dt><dd>${prods.map((p) => `${esc(p.name)}${p.url ? ` <span class="mono fine">${esc(p.url)}</span>` : ""} (${p.runs} run${p.runs === 1 ? "" : "s"}${p.used ? ", compared" : ", left out"})`).join("<br>")}</dd></div>` : ""}
        <div><dt>Shopper profiles</dt><dd>${m.personas.map((p) => `<span class="mono">${esc(p)}</span>`).join(", ") || "n/a"}</dd></div>
        <div><dt>Repeats per profile</dt><dd>${m.repeats}</dd></div>
        <div><dt>Sessions</dt><dd>${Object.entries(m.runs_by_flow).map(([k, n]) => `${n} ${esc(k)}`).join(", ")}</dd></div>
        <div><dt>Recorded</dt><dd>${m.steps_recorded} page states, ${m.screenshots_recorded} screenshots</dd></div>
        <div><dt>Prices</dt><dd>Read from the page at every step; the payable total is taken at the payment screen.</dd></div>
        <div><dt>Personalised pricing</dt><dd>Least-squares model on log price by device, country and visitor type; Benjamini-Hochberg correction; flagged only if p<sub>adj</sub> &lt; 0.05 and the effect is at least 2%${m.noise_sd_pct != null ? `. Residual noise in this audit: ${m.noise_sd_pct}% (1 sd)` : ""}.</dd></div>
        <div><dt>Dark-pattern copy</dt><dd>TF-IDF (word and character n-grams) with logistic regression, trained on labelled interface text.</dd></div>
        <div><dt>Policies</dt><dd>The site's written policies are fetched and compared with what the sessions measured.</dd></div>
      </dl>
    </section>

    <section class="doc-sec">
      <h2><span class="sec-n">5</span>Limitations</h2>
      <ul class="limits">
        <li>These are measurements with evidence, not legal findings. Category names follow India's Guidelines for Prevention and Regulation of Dark Patterns, 2023 and are indicative.</li>
        <li>Automated shoppers see what an ordinary visitor with the same profile would see at that time. Prices and pages can change between visits.</li>
        <li>The text classifier and the policy comparison can be wrong. Each finding names how it was produced so it can be checked against its exhibits.</li>
        ${r.notes.map((n) => `<li>${esc(n)}</li>`).join("")}
      </ul>
    </section>

    ${list.length ? `<section class="doc-sec exhibits">
      <h2><span class="sec-n">6</span>Exhibits</h2>
      <div class="ex-grid">${list.map((x) => `<figure><img src="/files/${esc(x.src)}" alt="Exhibit E-${x.n}" loading="eager"><figcaption><b class="mono">E-${x.n}</b> ${esc(x.step)}${x.text ? ` · ${esc(x.text.slice(0, 90))}` : ""}</figcaption></figure>`).join("")}</div>
    </section>` : ""}

    <section class="doc-sec integrity">
      <h2><span class="sec-n">7</span>Integrity</h2>
      <p>The fingerprint below is a SHA-256 over the report data and every exhibit. Recomputing it from the stored files shows whether anything changed after this report was issued.</p>
      <p class="fp"><span>Fingerprint</span><code>${esc(d.fingerprint)}</code></p>
      <table class="hashes"><thead><tr><th>File</th><th>SHA-256</th></tr></thead><tbody>${d.files.map((f) => `<tr><td class="mono">${esc(f.path)}</td><td class="mono">${esc(f.sha256 || "missing")}</td></tr>`).join("")}</tbody></table>
    </section>

    <div id="letterSlot"></div>

    <footer class="doc-foot"><span>${esc(d.reference)}</span><span>Pardarshi · generated ${esc(utc(new Date().toISOString()))}</span></footer>`;
  $("sheet").setAttribute("aria-busy", "false");
}

$("printBtn").onclick = () => window.print();
$("addLetter").onclick = async () => {
  const b = $("addLetter");
  b.disabled = true; b.textContent = "Writing the letter…";
  try {
    const u = PD.user || {};
    const out = await post(`/api/audits/${encodeURIComponent(ID)}/complaint`, { name: u.complaint_name || (u.is_guest ? "[Your name]" : u.name) || "[Your name]" });
    $("letterSlot").innerHTML = `<section class="doc-sec letter"><h2><span class="sec-n">A</span>Annex: complaint letter</h2><p class="fine">Draft for India's National Consumer Helpline (consumerhelpline.gov.in, or call 1915). Written by ${esc(out.method)} from this report's findings only. Check it before sending, and attach the exhibits.</p><pre>${esc(out.text)}</pre></section>`;
    b.textContent = "Letter attached";
    $("letterSlot").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    b.disabled = false; b.textContent = "Attach complaint letter";
    alert(e.message);
  }
};

(async function boot() {
  const u = await PD.ready;
  if (!u) { location.href = "/"; return; }
  if (!ID) { $("loading").textContent = "No audit selected. Open a report from the console."; return; }
  $("backLink").href = `/app?audit=${encodeURIComponent(ID)}`;
  try {
    [R.report, R.dossier] = await Promise.all([api(`/api/audits/${encodeURIComponent(ID)}`), api(`/api/audits/${encodeURIComponent(ID)}/dossier`)]);
    render();
  } catch (e) {
    $("loading").textContent = e.status === 404 ? "This report was not found, or it belongs to another account." : `Could not load the report: ${e.message}`;
  }
})();
