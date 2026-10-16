"use strict";
/* WebGL scenes on a locally vendored Three.js (r128), so they work offline and under the CSP.

   PriceStack  (landing)  The drip-pricing story in 3D: a price slab, then fee and add-on slabs land on it and the
                          "You pay" counter climbs. Agent orbs circle the stack. Drag to rotate, hover to inspect,
                          click to pull the layers apart.
   ScoreRing   (console)  A 3D gauge whose arc fills to the audit's transparency score.

   Both pause when off screen or when the tab is hidden, render a single still frame under
   prefers-reduced-motion, and report failure (no WebGL) so the page can show its static fallback. */

const Scene3D = (() => {
  const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const easeOut = (t) => 1 - Math.pow(1 - clamp(t, 0, 1), 3);
  const easeBack = (t) => { t = clamp(t, 0, 1); const c = 1.4; return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2); };
  const smooth = (a, b, t) => easeOut((t - a) / (b - a));

  function webglOK() {
    try { const c = document.createElement("canvas"); return !!(window.THREE && (c.getContext("webgl2") || c.getContext("webgl"))); }
    catch (e) { return false; }
  }
  const cvar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim() || "#888";
  const col = (n) => new THREE.Color(cvar(n));
  const isDark = () => PD.currentTheme() === "dark";

  function glowTexture(hex) {
    const c = document.createElement("canvas"); c.width = c.height = 128;
    const g = c.getContext("2d"), grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    grad.addColorStop(0, hex); grad.addColorStop(0.35, hex + "55"); grad.addColorStop(1, hex + "00");
    g.fillStyle = grad; g.fillRect(0, 0, 128, 128);
    const t = new THREE.CanvasTexture(c); t.needsUpdate = true; return t;
  }

  /** True when WebGL is emulated on the CPU (no GPU acceleration): SwiftShader, llvmpipe, Microsoft Basic Render. */
  function softwareGL() {
    try {
      const gl = document.createElement("canvas").getContext("webgl");
      const ext = gl.getExtension("WEBGL_debug_renderer_info");
      const name = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : "";
      return /swiftshader|llvmpipe|software|basic render/i.test(name);
    } catch (e) { return false; }
  }
  const SOFT = softwareGL();
  // CPU-composited pages choke on backdrop blur and animated gradients: switch those off (see .lowfx in premium.css).
  if (SOFT) document.documentElement.classList.add("lowfx");

  /** Shared plumbing: renderer, resize, visibility-aware loop, theme hook. */
  function rig(canvas, { fov, pos, look }) {
    // Software rendering is 10-50x slower: start light (no anti-aliasing, 1x resolution, 30 fps) instead of freezing the page.
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: !SOFT, alpha: true, powerPreference: "low-power" });
    renderer.setClearColor(0x000000, 0);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(fov, 1, 0.1, 100);
    camera.position.set(...pos); camera.lookAt(...look);
    const api = { renderer, scene, camera, onFrame: null, onTheme: null, visible: true, running: false, t0: performance.now(), cost: 0, soft: SOFT,
      pr: SOFT ? 1 : Math.min(devicePixelRatio || 1, 1.75), cap: SOFT ? 33 : 0 };
    Scene3D.instances.push(api);
    const resize = () => {
      const w = canvas.clientWidth || 300, h = canvas.clientHeight || 300;
      renderer.setPixelRatio(api.pr);
      renderer.setSize(w, h, false);
      camera.aspect = w / h; camera.updateProjectionMatrix();
      api.render();
    };
    api.render = () => renderer.render(scene, camera);
    api.start = () => {
      if (api.running || reduced()) return;
      api.running = true;
      let last = 0, slow = 0;
      const loop = (now) => {
        if (!api.running) return;
        requestAnimationFrame(loop);
        if (!api.visible || document.hidden || now - last < api.cap) return;
        const c0 = performance.now();
        api.onFrame && api.onFrame((c0 - api.t0) / 1000); api.render();
        last = now;
        api.cost = api.cost * 0.8 + (performance.now() - c0) * 0.2;
        // Adaptive quality: machines without GPU acceleration render in software. If frames are slow,
        // first lower the resolution, then the frame rate, so the page stays responsive.
        slow = performance.now() - c0 > 32 ? slow + 1 : Math.max(0, slow - 1);
        if (slow > 12) {
          slow = 0;
          if (api.pr > 1) { api.pr = 1; resize(); }
          else api.cap = Math.min(80, api.cap + 20);
        }
      };
      requestAnimationFrame(loop);
    };
    new ResizeObserver(resize).observe(canvas);
    new IntersectionObserver((es) => { api.visible = es[0].isIntersecting; }, { threshold: 0.01 }).observe(canvas);
    PD.onTheme(() => { api.onTheme && api.onTheme(); api.render(); });
    resize();
    return api;
  }

  /* ============================================================ PriceStack */
  const ADV = 996, FEES = 160, ADDON = 149, TOTAL = ADV + FEES + ADDON;  // matches the sample audit: 996 -> 1,305 (+31%)

  function slabGeometry(w, d, t, r = 0.28) {
    const s = new THREE.Shape(), x = -w / 2, y = -d / 2;
    s.moveTo(x + r, y); s.lineTo(x + w - r, y); s.absarc(x + w - r, y + r, r, -Math.PI / 2, 0);
    s.lineTo(x + w, y + d - r); s.absarc(x + w - r, y + d - r, r, 0, Math.PI / 2);
    s.lineTo(x + r, y + d); s.absarc(x + r, y + d - r, r, Math.PI / 2, Math.PI);
    s.lineTo(x, y + r); s.absarc(x + r, y + r, r, Math.PI, Math.PI * 1.5);
    const g = new THREE.ExtrudeGeometry(s, { depth: t, bevelEnabled: true, bevelThickness: 0.03, bevelSize: 0.03, bevelSegments: 3, curveSegments: 14 });
    g.rotateX(-Math.PI / 2);  // extrusion axis -> up
    return g;
  }

  function PriceStack(canvas, ui) {
    if (!webglOK()) return null;
    const R = rig(canvas, { fov: 30, pos: [6.6, 4.7, 8.2], look: [0, 1.0, 0] });
    const { scene, camera } = R;
    const world = new THREE.Group(); scene.add(world);

    scene.add(new THREE.AmbientLight(0xffffff, 0.75));
    const key = new THREE.DirectionalLight(0xffffff, 0.95); key.position.set(5, 8, 6); scene.add(key);
    const rim = new THREE.PointLight(col("--accent"), 1.1, 22); rim.position.set(-5, 3, -4); scene.add(rim);

    // floor: faint grid + a soft glow that anchors the stack
    const grid = new THREE.GridHelper(16, 32, 0x888888, 0x888888);
    grid.material.transparent = true; grid.position.y = -0.04; world.add(grid);
    const glow = new THREE.Mesh(new THREE.PlaneGeometry(9, 9), new THREE.MeshBasicMaterial({ transparent: true, depthWrite: false }));
    glow.rotation.x = -Math.PI / 2; glow.position.y = -0.03; world.add(glow);

    // slabs (thickness reads as the amount)
    const defs = [
      { key: "price", c: "--s1", t: ADV / 1100, name: "Price", amount: ADV },
      { key: "fees", c: "--s2", t: 0.16, name: "Fees & taxes", amount: FEES },
      { key: "addon", c: "--s3", t: 0.14, name: "Add-on, pre-ticked", amount: ADDON },
    ];
    const slabs = defs.map((d) => {
      const geo = slabGeometry(3.7, 2.5, d.t);
      const mat = new THREE.MeshStandardMaterial({ color: col(d.c), roughness: 0.3, metalness: 0.12, transparent: true, opacity: 0.9, emissive: col(d.c), emissiveIntensity: 0.05 });
      const mesh = new THREE.Mesh(geo, mat);
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo, 35), new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.5 }));
      mesh.add(edges); mesh.userData = { ...d, edges }; world.add(mesh);
      return mesh;
    });

    // agent orbs on tilted orbits
    const orbColors = ["--accent", "--s2", "--s3", "--s1", "--s2", "--s3"];
    const orbs = orbColors.map((c, i) => {
      const radius = 3.5 + (i % 3) * 0.55, tilt = (i - 2.5) * 0.28, speed = (0.28 + (i % 3) * 0.07) * (i % 2 ? -1 : 1), phase = i * 1.05;
      const m = new THREE.Mesh(new THREE.SphereGeometry(0.11, 20, 20), new THREE.MeshBasicMaterial({ color: col(c) }));
      const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture("#ffffff"), color: col(c), transparent: true, opacity: 0.55, depthWrite: false, blending: THREE.AdditiveBlending }));
      halo.scale.set(0.9, 0.9, 1); m.add(halo);
      const ring = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(Array.from({ length: 96 }, (_, k) => new THREE.Vector3(Math.cos((k / 96) * 6.2832) * radius, 0, Math.sin((k / 96) * 6.2832) * radius))), new THREE.LineBasicMaterial({ color: col(c), transparent: true, opacity: 0.16 }));
      const pivot = new THREE.Group(); pivot.rotation.x = tilt; pivot.rotation.z = i * 0.35; pivot.add(ring); pivot.add(m); world.add(pivot);
      return { m, halo, ring, radius, speed, phase, c };
    });
    // scan beams from an orb to the stack
    const beams = Array.from({ length: 3 }, () => {
      const l = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]), new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0 }));
      l.frustumCulled = false; scene.add(l); return { l, born: -9, orb: null };
    });

    // HTML labels that follow the slabs
    const labelEls = defs.map((d) => {
      const el = document.createElement("div"); el.className = "st-label";
      el.innerHTML = `<i style="background:var(${d.c})"></i><span>${d.name}</span><b>${d.key === "price" ? "" : "+"}₹${d.amount.toLocaleString("en-IN")}</b>`;
      ui.stageUi.append(el); return el;
    });

    // interaction state
    const S = { rotY: -0.5, rotX: 0.0, vel: 0, dragging: false, lastX: 0, idle: 0, exploded: false, explode: 0, hover: -1, playT: 0, mx: 0, my: 0 };
    canvas.style.touchAction = "pan-y";
    canvas.addEventListener("pointerdown", (e) => { S.dragging = true; S.lastX = e.clientX; S.idle = performance.now(); canvas.setPointerCapture(e.pointerId); canvas.style.cursor = "grabbing"; S.moved = 0; });
    canvas.addEventListener("pointermove", (e) => {
      const r = canvas.getBoundingClientRect();
      S.mx = ((e.clientX - r.left) / r.width) * 2 - 1; S.my = ((e.clientY - r.top) / r.height) * 2 - 1;
      if (S.dragging) { const dx = e.clientX - S.lastX; S.rotY += dx * 0.0085; S.lastX = e.clientX; S.moved += Math.abs(dx); S.idle = performance.now(); }
      pick(e);
      if (reduced()) R.render();
    });
    const endDrag = (e) => { if (S.dragging && S.moved < 4) { S.exploded = !S.exploded; } S.dragging = false; canvas.style.cursor = S.hover >= 0 ? "pointer" : "grab"; try { canvas.releasePointerCapture(e.pointerId); } catch (x) { /* already released */ } if (reduced()) { S.explode = S.exploded ? 1 : 0; layout(99); R.render(); } };
    canvas.addEventListener("pointerup", endDrag); canvas.addEventListener("pointercancel", endDrag);
    canvas.addEventListener("pointerleave", () => { S.hover = -1; S.mx = S.my = 0; });
    canvas.style.cursor = "grab";
    const ray = new THREE.Raycaster(), v2 = new THREE.Vector2();
    function pick(e) {
      const r = canvas.getBoundingClientRect(); v2.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(v2, camera);
      const hit = ray.intersectObjects(slabs, false)[0];
      S.hover = hit ? slabs.indexOf(hit.object) : -1;
      if (!S.dragging) canvas.style.cursor = S.hover >= 0 ? "pointer" : "grab";
    }

    // timeline: slab y positions and appearance as a function of story time
    const baseY = [0, defs[0].t + 0.06, defs[0].t + defs[1].t + 0.12];
    const tmp = new THREE.Vector3();
    function layout(t) {
      const ex = S.explode;
      slabs.forEach((m, i) => {
        let appear, x = 0, y = baseY[i] + ex * i * 0.5, scale = 1, op = 0.9;
        if (i === 0) { appear = smooth(0.2, 1.2, t); y += (1 - easeBack((t - 0.2) / 1.0)) * -0.6; scale = 0.82 + 0.18 * easeOut((t - 0.2) / 1.0); }
        if (i === 1) { const p = (t - 1.6) / 1.1; appear = clamp(p * 3, 0, 1); x = (1 - easeOut(p)) * 6.5; }
        if (i === 2) { const p = (t - 3.2) / 1.0; appear = clamp(p * 4, 0, 1); y += (1 - easeBack(p)) * 3.2; }
        m.visible = appear > 0.001; m.position.set(x, y, 0); m.scale.setScalar(scale);
        m.material.opacity = op * appear; m.userData.edges.material.opacity = 0.5 * appear;
        m.material.emissiveIntensity = S.hover === i ? 0.45 : 0.05;
        m.userData.appear = appear;
      });
    }
    function counter(t) {
      const pay = ADV + FEES * easeOut((t - 1.9) / 0.9) + ADDON * easeOut((t - 3.5) / 0.7);
      ui.pay.textContent = "₹" + Math.round(pay).toLocaleString("en-IN");
      ui.drip.textContent = "+" + Math.round(((pay - ADV) / ADV) * 100) + "%";
      ui.dripWrap.style.opacity = pay > ADV + 4 ? 1 : 0;
    }
    // leader lines from each slab's edge to its label
    const leaders = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    leaders.setAttribute("class", "st-leaders");
    const leaderLines = defs.map((d) => { const l = document.createElementNS("http://www.w3.org/2000/svg", "path"); l.setAttribute("fill", "none"); l.setAttribute("stroke-width", "1.5"); l.style.stroke = `var(${d.c})`; leaders.append(l); return l; });
    ui.stageUi.prepend(leaders);

    function place() {
      const w = canvas.clientWidth, h = canvas.clientHeight;
      // project each slab's right edge; labels form one tidy column, kept 32px apart, top slab first
      const pts = slabs.map((m) => { tmp.set(1.9, m.position.y + m.userData.t / 2, 0).applyMatrix4(world.matrixWorld).project(camera); return { x: (tmp.x * 0.5 + 0.5) * w, y: (-tmp.y * 0.5 + 0.5) * h }; });
      const colW = Math.max(...labelEls.map((e) => e.offsetWidth || 150));
      const X = Math.min(Math.max(...pts.map((p) => p.x)) + 44, w - colW - 8);
      const order = [2, 1, 0], ys = [];
      order.forEach((i, k) => { ys[i] = k === 0 ? pts[i].y : Math.max(pts[i].y, ys[order[k - 1]] + 32); });
      leaders.setAttribute("viewBox", `0 0 ${w} ${h}`);
      labelEls.forEach((el, i) => {
        const m = slabs[i], on = m.userData.appear > 0.9 ? 1 : 0;
        el.style.transform = `translate(${X}px, ${ys[i] - 13}px)`; el.style.opacity = on;
        el.classList.toggle("hot", S.hover === i);
        leaderLines[i].setAttribute("d", `M${pts[i].x},${pts[i].y} L${X - 14},${ys[i]} L${X - 4},${ys[i]}`);
        leaderLines[i].style.opacity = on * (S.hover === i ? 1 : 0.6);
      });
    }

    let lastT = 0, beamClock = 0;
    function frame(time) {
      const dt = Math.min(0.05, time - lastT); lastT = time;
      const loopT = reduced() ? 99 : (time - S.playT) % 12.5;  // 12.5 s story, then replay
      const story = loopT > 11.2 ? 99 : loopT;  // 99 = the finished state, held while it fades before replaying
      // explode in the second half of the story; user click overrides
      const autoEx = loopT > 5.4 && loopT < 9.2 ? 1 : 0;
      const target = S.exploded ? 1 : autoEx;
      S.explode += (target - S.explode) * Math.min(1, dt * 3.2);
      layout(loopT > 11.2 ? 99 : story);
      counter(loopT > 11.2 ? 99 : story);
      if (loopT > 11.2) { const f = 1 - (loopT - 11.2) / 1.3; slabs.forEach((m) => { m.material.opacity *= f; m.userData.edges.material.opacity *= f; }); labelEls.forEach((el) => (el.style.opacity = 0)); }
      // camera motion: idle auto-rotate, mouse parallax, inertia after drag
      const idle = performance.now() - S.idle > 2500;
      if (!S.dragging && idle && !reduced()) S.rotY += dt * 0.12;
      world.rotation.y += (S.rotY - world.rotation.y) * 0.12;
      world.rotation.x += ((S.my * 0.08) - world.rotation.x) * 0.06;
      world.updateMatrixWorld(true);
      // orbs and beams
      orbs.forEach((o) => { const a = o.phase + time * o.speed; o.m.position.set(Math.cos(a) * o.radius, 0, Math.sin(a) * o.radius); o.halo.material.opacity = 0.4 + 0.2 * Math.sin(time * 2 + o.phase); });
      beamClock += dt;
      if (beamClock > 1.4 && story > 3.8) { beamClock = 0; const b = beams.find((x) => time - x.born > 0.9); if (b) { b.born = time; b.orb = orbs[(Math.random() * orbs.length) | 0]; b.l.material.color.copy(col(b.orb.c)); } }
      beams.forEach((b) => {
        const age = time - b.born; if (age > 0.9 || !b.orb) { b.l.material.opacity = 0; return; }
        const p = b.orb.m.getWorldPosition(new THREE.Vector3()), top = new THREE.Vector3(0, slabs[2].position.y + 0.1, 0).applyMatrix4(world.matrixWorld);
        const pos = b.l.geometry.attributes.position; pos.setXYZ(0, p.x, p.y, p.z); pos.setXYZ(1, top.x, top.y, top.z); pos.needsUpdate = true;
        b.l.material.opacity = 0.75 * Math.sin((age / 0.9) * Math.PI);
      });
      place();
    }
    R.onFrame = frame;

    function applyTheme() {
      const dark = isDark(), bg = cvar("--plane");
      scene.fog = new THREE.FogExp2(new THREE.Color(bg), 0.045);
      grid.material.opacity = dark ? 0.18 : 0.26; grid.material.color.set(dark ? 0x6b6455 : 0xc2b79d);
      glow.material.map = glowTexture(cvar("--accent")); glow.material.opacity = dark ? 0.55 : 0.4; glow.material.needsUpdate = true;
      slabs.forEach((m) => m.userData.edges.material.color.set(dark ? 0xffffff : 0x15171f));
      slabs.forEach((m) => (m.userData.edges.material.opacity = dark ? 0.5 : 0.28));
      rim.color = col("--accent");
    }
    R.onTheme = applyTheme; applyTheme();

    R.replay = () => { S.playT = (performance.now() - R.t0) / 1000; S.exploded = false; };
    R.pause = () => { R.running = false; };
    /** Render the story at exactly t seconds (used by tests and screenshots). */
    R.seek = (t) => {
      const now = (performance.now() - R.t0) / 1000;
      S.playT = now - t; S.explode = S.exploded || (t > 5.4 && t < 9.2) ? 1 : 0;
      frame(now); R.render();
    };
    if (reduced()) { S.explode = 0; layout(99); counter(99); world.updateMatrixWorld(true); place(); R.render(); }
    R.start();
    return R;
  }

  /* ============================================================ ScoreRing */
  function ScoreRing(canvas) {
    if (!webglOK()) return null;
    const R = rig(canvas, { fov: 32, pos: [0, 0, 5.6], look: [0, 0, 0] });
    const { scene } = R;
    scene.add(new THREE.AmbientLight(0xffffff, 0.8));
    const key = new THREE.DirectionalLight(0xffffff, 0.9); key.position.set(2, 3, 5); scene.add(key);
    const rim = new THREE.PointLight(0xffffff, 1.0, 14); rim.position.set(-3, -2, 3); scene.add(rim);
    const g = new THREE.Group(); scene.add(g);

    const RAD = 1.28;
    const track = new THREE.Mesh(new THREE.TorusGeometry(RAD, 0.1, 24, 160), new THREE.MeshStandardMaterial({ roughness: 0.6, metalness: 0.1, transparent: true, opacity: 0.4 }));
    g.add(track);
    const arcMat = new THREE.MeshStandardMaterial({ roughness: 0.28, metalness: 0.2, emissiveIntensity: 0.35 });
    const arc = new THREE.Mesh(new THREE.BufferGeometry(), arcMat); arc.scale.x = -1; arc.rotation.z = -Math.PI / 2; g.add(arc);
    const cap = new THREE.Mesh(new THREE.SphereGeometry(0.17, 24, 24), arcMat); g.add(cap);
    const halo = new THREE.Sprite(new THREE.SpriteMaterial({ transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, opacity: 0.8 })); halo.scale.set(1.1, 1.1, 1); g.add(halo);
    // drifting particles in a band around the ring
    const N = 90, pos = new Float32Array(N * 3), seeds = [];
    for (let i = 0; i < N; i++) { const a = Math.random() * 6.283, r = 1.6 + Math.random() * 0.55; seeds.push({ a, r, z: (Math.random() - 0.5) * 0.7, s: 0.05 + Math.random() * 0.12 }); }
    const pg = new THREE.BufferGeometry(); pg.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    const pts = new THREE.Points(pg, new THREE.PointsMaterial({ size: 0.035, transparent: true, opacity: 0.7, depthWrite: false })); g.add(pts);

    const S = { score: 0, shown: 0, color: new THREE.Color("#27358f"), t0: performance.now(), mx: 0, my: 0, built: -1 };
    canvas.addEventListener("pointermove", (e) => { const r = canvas.getBoundingClientRect(); S.mx = ((e.clientX - r.left) / r.width) * 2 - 1; S.my = ((e.clientY - r.top) / r.height) * 2 - 1; if (reduced()) { tilt(); R.render(); } });
    canvas.addEventListener("pointerleave", () => { S.mx = S.my = 0; if (reduced()) { tilt(); R.render(); } });
    canvas.addEventListener("click", () => R.setScore(S.score, S.colorVar, true));
    canvas.style.cursor = "pointer";

    function build(value) {
      const q = Math.round(value * 2) / 2;
      if (q === S.built) return; S.built = q;
      arc.geometry.dispose();
      const a = Math.max(0.0001, (q / 100) * Math.PI * 2);
      arc.geometry = new THREE.TorusGeometry(RAD, 0.15, 24, 160, a);
      const ang = Math.PI / 2 - a;  // end of the arc (clockwise from the top)
      cap.position.set(Math.cos(ang) * RAD, Math.sin(ang) * RAD, 0); halo.position.copy(cap.position);
      cap.visible = halo.visible = q > 0.5;
    }
    function tilt() { g.rotation.y += ((S.mx * 0.5) - g.rotation.y) * 0.1; g.rotation.x += ((S.my * 0.4) - g.rotation.x) * 0.1; }
    R.onFrame = (time) => {
      const k = easeOut((performance.now() - S.t0) / 1500);
      S.shown = S.score * k; build(S.shown);
      tilt();
      g.rotation.z = Math.sin(time * 0.5) * 0.03;
      const arr = pg.attributes.position.array;
      seeds.forEach((p, i) => { const a = p.a + time * p.s; arr[i * 3] = Math.cos(a) * p.r; arr[i * 3 + 1] = Math.sin(a) * p.r; arr[i * 3 + 2] = p.z + Math.sin(time + i) * 0.04; });
      pg.attributes.position.needsUpdate = true;
    };
    function paint() {
      const dark = isDark();
      arcMat.color.copy(S.color); arcMat.emissive.copy(S.color);
      track.material.color.set(dark ? 0x4a4538 : 0xcdc3aa);
      halo.material.map = glowTexture("#" + S.color.getHexString()); halo.material.needsUpdate = true;
      pts.material.color.copy(S.color); rim.color.copy(S.color);
    }
    R.onTheme = () => { if (S.colorVar) S.color = col(S.colorVar); paint(); };
    R.pause = () => { R.running = false; };
    /** Jump to the finished state and render once (used by tests and screenshots). */
    R.freeze = () => { R.running = false; S.t0 = performance.now() - 5000; R.onFrame((performance.now() - R.t0) / 1000); R.render(); };
    /** colorVar: a CSS custom property name such as "--crit". */
    R.setScore = (score, colorVar, replay) => {
      S.score = clamp(score, 0, 100); S.colorVar = colorVar; S.color = col(colorVar); paint();
      if (replay || S.built < 0) S.t0 = performance.now();
      if (reduced()) { S.shown = S.score; S.built = -1; build(S.score); tilt(); R.render(); }
    };
    paint(); R.start();
    return R;
  }

  return { PriceStack, ScoreRing, webglOK, instances: [], isSoftware: () => SOFT };
})();
