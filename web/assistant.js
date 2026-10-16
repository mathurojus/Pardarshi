"use strict";
/* Audit assistant panel: text + voice in, text + voice out.

   Modes
     "audit"  asks /api/audits/:id/ask, answers come from THIS audit's own data (exact local engine, or Gemini).
     "agent"  the Google ADK agent, which can also run new audits.

   Voice input uses the browser's SpeechRecognition (Chrome, Edge, Safari). The browser's speech service does the
   transcribing; Pardarshi never records or stores audio. Voice output uses speechSynthesis. */

const Assistant = (() => {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const synth = window.speechSynthesis;
  const ICON = {
    mic: '<svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3" fill="none" stroke="currentColor" stroke-width="2"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M9 21h6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    stop: '<svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="3" fill="currentColor"/></svg>',
    speaker: '<svg width="17" height="17" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5H4Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    play: '<svg width="13" height="13" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4.5v15l12-7.5-12-7.5Z" fill="currentColor"/></svg>',
    clear: '<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 7h14M10 7V4.5h4V7M7 7l1 12.5h8L17 7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };
  const LOCALE = { IN: "en-IN", US: "en-US", GB: "en-GB", CA: "en-CA", AU: "en-AU", DE: "de-DE", FR: "fr-FR", JP: "ja-JP" };
  const st = { report: null, mode: "audit", speak: false, listening: false, rec: null, busy: false, session: null, logs: new Map(), heard: "" };

  try { st.speak = localStorage.getItem("pd-speak") === "1"; } catch (e) { /* storage blocked */ }
  const lang = () => LOCALE[(PD.user && PD.user.country) || ""] || navigator.language || "en-US";
  const key = () => `${st.report ? st.report.audit_id : "none"}|${st.mode}`;
  const log = () => { const k = key(); if (!st.logs.has(k)) st.logs.set(k, []); return st.logs.get(k); };
  const note = (t) => { $("asNote").textContent = t || ""; };

  /* ---------- text to speech ---------- */
  function speakable(text) {
    return text
      .replace(/[*_`#>]/g, "")
      .replace(/₹\s?([\d,]+(?:\.\d+)?)/g, (m, n) => `${n.replace(/,/g, "")} rupees`)
      .replace(/\+(\d)/g, "plus $1").replace(/(\d)%/g, "$1 percent").replace(/\s{2,}/g, " ").trim();
  }
  function say(text) {
    if (!synth) return;
    synth.cancel();
    const u = new SpeechSynthesisUtterance(speakable(text));
    u.lang = lang(); u.rate = 1.02;
    const v = synth.getVoices().find((x) => x.lang === u.lang) || synth.getVoices().find((x) => x.lang.startsWith(u.lang.slice(0, 2)));
    if (v) u.voice = v;
    synth.speak(u);
  }
  const stopSpeaking = () => { if (synth) synth.cancel(); };

  /* ---------- rendering ---------- */
  function msgEl(m) {
    const d = document.createElement("div");
    d.className = "msg " + (m.role === "user" ? "u" + (m.voice ? " voice" : "") : "a");
    d.append(document.createTextNode(m.text));
    if (m.role === "bot") {
      const meta = document.createElement("div"); meta.className = "meta";
      if (m.trace && m.trace.length) {
        const tr = document.createElement("div"); tr.className = "trace"; tr.style.marginBottom = "0";
        m.trace.forEach((t) => { const s = document.createElement("span"); s.textContent = `${t.agent} → ${t.tool}`; tr.append(s); });
        d.prepend(tr); d.firstChild.style.marginBottom = "8px";
      }
      if (m.engine) { const e = document.createElement("span"); e.className = "engine"; e.textContent = m.engine; meta.append(e); }
      if (synth && !m.error) {
        const b = document.createElement("button"); b.type = "button"; b.innerHTML = ICON.play + "<span>Listen</span>"; b.setAttribute("aria-label", "Read this answer aloud");
        b.onclick = () => say(m.text); meta.append(b);
      }
      if (meta.childNodes.length) d.append(meta);
    }
    return d;
  }
  function scroll() { const l = $("asLog"); l.scrollTop = l.scrollHeight; }
  function render() {
    const l = $("asLog"); l.replaceChildren();
    log().forEach((m) => l.append(m.system ? sysEl(m.text) : msgEl(m)));
    scroll();
  }
  const sysEl = (t) => { const d = document.createElement("div"); d.className = "sysnote"; d.textContent = t; return d; };
  function suggestions(list) {
    const box = $("asSuggest"); box.replaceChildren();
    (list || []).slice(0, 4).forEach((q) => { const b = document.createElement("button"); b.type = "button"; b.textContent = q; b.onclick = () => ask(q, false); box.append(b); });
  }
  const START = ["Give me a summary", "What are the hidden fees?", "Does the price depend on who I am?", "How hard is it to cancel?"];
  const AGENT_START = ["Audit the clean shop quickly and compare it with the dirty one", "Which of my audits is the worst?", "Draft a complaint for the latest dirty audit"];

  function welcome() {
    const r = st.report;
    if (st.mode === "audit") {
      log().push({ role: "bot", engine: "From the audit data", text: `I know this whole audit of ${r.site}. It scores ${Math.round(r.trust_score)} out of 100 and flagged ${r.findings.length} ${r.findings.length === 1 ? "thing" : "things"}. Ask me anything about it, by typing or with the microphone.` });
      suggestions(START);
    } else {
      log().push({ role: "bot", engine: "Agent", text: "I am the Pardarshi agent. I can run new audits, compare sites and draft complaints. I use Gemini, so a full audit can take a minute." });
      suggestions(AGENT_START);
    }
  }

  /* ---------- asking ---------- */
  async function ask(text, viaVoice) {
    text = (text || "").trim();
    if (!text || st.busy || !st.report) return;
    stopSpeaking();
    st.busy = true;
    const h = log();
    h.push({ role: "user", text, voice: !!viaVoice });
    render();
    suggestions([]);
    const wait = document.createElement("div"); wait.className = "msg a"; wait.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
    $("asLog").append(wait); scroll();
    let out;
    try {
      if (st.mode === "audit") {
        const history = h.slice(-7, -1).filter((m) => !m.system).map((m) => ({ role: m.role === "user" ? "user" : "assistant", text: m.text }));
        const r = await post(`/api/audits/${encodeURIComponent(st.report.audit_id)}/ask`, { question: text, history });
        out = { role: "bot", text: r.text, engine: r.method === "gemini" ? "Gemini" : "From the audit data", suggestions: r.suggestions };
      } else {
        const r = await post("/api/agent", { message: text, session_id: st.session });
        st.session = r.session_id;
        out = { role: "bot", text: r.reply || "The agent did not return an answer.", engine: "Agent", trace: r.trace };
        if (typeof loadBoard === "function") loadBoard();
      }
    } catch (e) {
      out = { role: "bot", text: e.message || "Something went wrong.", engine: "Error", error: true };
    }
    h.push(out);
    st.busy = false;
    render();
    suggestions(out.suggestions || (st.mode === "audit" ? START : AGENT_START));
    if (!out.error && (st.speak || viaVoice)) say(out.text);
  }

  /* ---------- voice input ---------- */
  const MIC_ERR = {
    "not-allowed": "Microphone access was blocked. Allow it from the lock icon in the address bar, then try again.",
    "service-not-allowed": "Microphone access was blocked. Allow it from the lock icon in the address bar, then try again.",
    "no-speech": "I did not hear anything. Tap the microphone and speak after it turns red.",
    "audio-capture": "No microphone was found on this device.",
    network: "Voice recognition needs an internet connection in this browser.",
    aborted: "",
  };
  function setListening(on) {
    st.listening = on;
    const b = $("asMic");
    b.classList.toggle("on", on); b.setAttribute("aria-pressed", String(on));
    b.innerHTML = on ? ICON.stop : ICON.mic;
    b.setAttribute("aria-label", on ? "Stop listening" : "Ask by voice");
    $("asWave").classList.toggle("show", on);
    $("asHeard").textContent = on ? "Listening…" : "";
  }
  function listen() {
    if (!SR) return;
    if (st.listening) { try { st.rec.stop(); } catch (e) { /* already stopped */ } return; }
    stopSpeaking(); note("");
    const rec = new SR();
    rec.lang = lang(); rec.interimResults = true; rec.continuous = false; rec.maxAlternatives = 1;
    let finalText = "";
    rec.onstart = () => { setListening(true); note("Your browser's speech service transcribes what you say. Pardarshi does not record audio."); };
    rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript;
        if (e.results[i].isFinal) finalText += t; else interim += t;
      }
      $("asInput").value = (finalText + interim).trim();
      $("asHeard").textContent = ((finalText + interim).trim() || "Listening…").slice(-70);
    };
    rec.onerror = (e) => { if (MIC_ERR[e.error] !== undefined) note(MIC_ERR[e.error]); else note(`Voice input stopped (${e.error}).`); };
    rec.onend = () => {
      setListening(false);
      const said = (finalText || $("asInput").value).trim();
      if (said) { $("asInput").value = ""; ask(said, true); }
    };
    st.rec = rec;
    try { rec.start(); } catch (e) { setListening(false); note("Voice input could not start. Try again."); }
  }

  /* ---------- wiring ---------- */
  function init() {
    $("asMic").innerHTML = ICON.mic; $("asSpeak").innerHTML = ICON.speaker; $("asClear").innerHTML = ICON.clear;
    $("asSpeak").setAttribute("aria-pressed", String(st.speak));
    if (!SR) { $("asMic").disabled = true; $("asMic").title = "Voice input is not supported in this browser"; note("Voice input needs Chrome, Edge or Safari. Typing works everywhere."); }
    if (!synth) { $("asSpeak").disabled = true; $("asSpeak").title = "Read-aloud is not supported in this browser"; }
    $("asMic").onclick = listen;
    $("asSpeak").onclick = () => {
      st.speak = !st.speak; $("asSpeak").setAttribute("aria-pressed", String(st.speak));
      try { localStorage.setItem("pd-speak", st.speak ? "1" : "0"); } catch (e) { /* storage blocked */ }
      if (!st.speak) stopSpeaking(); note(st.speak ? "Answers will be read aloud." : "");
    };
    $("asClear").onclick = () => { stopSpeaking(); st.logs.delete(key()); if (st.mode === "agent") st.session = null; if (st.report) { welcome(); render(); } };
    $("asForm").onsubmit = (e) => { e.preventDefault(); const v = $("asInput").value; $("asInput").value = ""; ask(v, false); };
    document.querySelectorAll('input[name="asmode"]').forEach((r) => (r.onchange = () => {
      st.mode = r.value; $("asInput").placeholder = st.mode === "audit" ? "Ask anything about this audit" : "Ask the agent to run or compare audits";
      if (!log().length && st.report) welcome();
      render(); suggestions(st.mode === "audit" ? START : AGENT_START);
    }));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") { if (st.listening) { try { st.rec.abort(); } catch (x) { /* ignore */ } } stopSpeaking(); } });
    if (synth) synth.getVoices();  // warm up the voice list
  }

  /** Called whenever a different audit is shown. Keeps a separate conversation per audit. */
  function setReport(r) {
    const first = !st.report || st.report.audit_id !== r.audit_id;
    st.report = r;
    $("asCtx").textContent = `${r.site}  ·  ${Math.round(r.trust_score)}/100  ·  ${r.findings.length} findings`;
    if (!log().length) welcome();
    else if (first) log().push({ system: true, text: `Now discussing ${r.site}` });
    render();
    if (!st.busy) suggestions(st.mode === "audit" ? START : AGENT_START);
  }

  init();
  return { setReport, ask, _state: st };
})();
