/* AIMS console: one page, no build step. Talks to backend/app.py (REST + WebSocket); the event
   contract is in backend/runner.py. */
const $ = (id) => document.getElementById(id);
let CFG = null, currentLoop = "navigation", currentRun = null, ws = null, lastFinal = "", ncalls = 0;
let pendingApproval = null;
/* Three ways this page can run. Same origin as the console service (the lab machine): plain fetches.
   A static copy with window.AIMS_BACKEND set (leonmgarcia.com/AIMS): every call goes to that host, and
   the browser that starts a run is the only one that sees it (a client id). A static copy with
   window.AIMS_STATIC and no reachable backend: the recorded runs are replayed. */
const BACKEND = (window.AIMS_BACKEND || (() => { try { return localStorage.getItem("aims-backend") || ""; } catch (e) { return ""; } })()).replace(/\/$/, "");
/* Served by the console itself behind a path prefix (a National Compute workspace exposes a port at
   .../port/8080/, a reverse proxy may mount it at /AIMS/): every call keeps the page's own prefix. */
const PAGE_PREFIX = location.pathname.endsWith("/") ? location.pathname.slice(0, -1) : location.pathname.replace(/\/[^/]*$/, "");
const SERVICE = BACKEND || PAGE_PREFIX;
let STATIC = null;                       // set when the page falls back to the recorded runs
const RECORDED = window.AIMS_STATIC || null;
let replayTimers = [];
const CLIENT = (() => { try { let c = localStorage.getItem("aims-client"); if (!c) { c = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2)); localStorage.setItem("aims-client", c); } return c; } catch (e) { return "anon-" + Math.random().toString(36).slice(2); } })();
const TOKEN = window.AIMS_TOKEN || "";     // the service's access token, carried inside the encrypted static page
const KEYS = {anthropic: "", openai: ""};
try { KEYS.anthropic = sessionStorage.getItem("aims-key-anthropic") || ""; KEYS.openai = sessionStorage.getItem("aims-key-openai") || ""; } catch (e) {}

const LOOPS = {
  navigation: {n: "01", title: "Navigation", cls: "s1",
    text: "Find the device from the marker template: scan, estimate the position with the particle filter, move the stage, repeat until centred."},
  measurement: {n: "02", title: "Measurement", cls: "s2",
    text: "Set the scan frame on the device, ramp the bottom gate, acquire the gate series and the grid spectra."},
  discovery: {n: "03", title: "Discovery", cls: "s3",
    text: "Analyse the packet: melting temperatures, twist disorder, hypotheses, evidence."},
};

async function api(path, opts) {
  if (STATIC) {
    if (path === "/api/config") return STATIC.config;
    if (path === "/api/tools") return {ok: true, tools: STATIC.tools};
    if (path.startsWith("/api/runs")) return STATIC.runs;
    return {error: "static copy"};
  }
  const sep = path.includes("?") ? "&" : "?";
  const headers = {"Content-Type": "application/json"};
  if (TOKEN) headers["X-AIMS-Token"] = TOKEN;
  const r = await fetch(`${SERVICE}${path}${sep}client=${encodeURIComponent(CLIENT)}`,
                        Object.assign({headers}, opts || {}));
  let body;
  try { body = await r.json(); } catch (e) { body = {}; }
  if (!r.ok && body && body.detail && !body.error) body.error = body.detail;
  return body;
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

/* ---------- setup ---------- */
async function init() {
  let denied = "";
  try {
    CFG = await api("/api/config");
    if (CFG && CFG.error && /access token/.test(CFG.error)) denied = CFG.error;
    if (!CFG || !CFG.loops) throw new Error("no config");
  } catch (e) {
    // the reader sees one address only, this page's; the service's goes to the console log
    if (BACKEND) console.log("console service", BACKEND, denied ? "refused the token" : "not reachable");
    const why = denied ? "The console service needs its access token, and this page was built without it (static/build_static.py --token). "
                       : "The console service is not reachable right now. ";
    if (!RECORDED) { $("byline").textContent = why.trim(); return; }
    STATIC = RECORDED;
    CFG = STATIC.config;
    const n = $("staticnote");
    n.textContent = (BACKEND || denied ? why : "No console service is connected. ") +
      (STATIC.note || "This page replays the recorded simulator runs; nothing here reaches an instrument or an agent.");
    n.hidden = false;
  }
  serviceRow();
  const mode = $("mode");
  mode.textContent = CFG.sim ? "SIMULATION" : "HARDWARE";
  mode.className = "tag mode " + (CFG.sim ? "sim" : "hw");
  const by = $("byline");
  by.textContent = "";
  const agent = CFG.byok ? "the model and key chosen below" : CFG.model;
  for (const [label, value] of [["MCP server", CFG.mcp_url || (CFG.sim_per_run ? "one simulator per run" : "none")], ["agent", agent], ["logs in", CFG.public ? "this browser's runs only" : (CFG.runs_dir || "runs_console").split("/").pop() + "/"]]) {
    const sp = el("span"); sp.append(label + " ", code(value)); by.appendChild(sp);
  }
  buildModels();
  if (CFG.byok || BACKEND) {
    $("keys").hidden = false;
    $("key_anthropic").value = KEYS.anthropic; $("key_openai").value = KEYS.openai;
    for (const p of ["anthropic", "openai"]) $("key_" + p).oninput = (e) => { KEYS[p] = e.target.value.trim(); try { sessionStorage.setItem("aims-key-" + p, KEYS[p]); } catch (x) {} };
  }
  if (CFG.public) {
    const n = $("publicnote");
    n.textContent = `Public console: every run gets its own simulated instrument, runs on the API key entered below, is capped at ${CFG.max_turns_cap} turns and ${CFG.wall_limit_min} minutes, and is visible only in this browser. The discovery loop (file and shell tools) runs with the downloadable console.`;
    n.hidden = false;
    $("maxturns").value = Math.min(parseInt($("maxturns").value, 10) || 200, CFG.max_turns_cap);
    $("turnnote").textContent = `at most ${CFG.max_turns_cap} here`;
  }
  if (!CFG.skills) { $("skillsrow").hidden = true; $("skillsnote").hidden = true; }
  $("useskills").onchange = () => selectLoop(currentLoop);
  if (STATIC) {
    for (const id of ["start", "stop", "send", "msg", "apallow", "apdeny", "apnote"]) $(id).disabled = true;
  }
  buildChain();
  selectLoop(currentLoop);
  refreshRuns();
  setTemplate(CFG.template);
  drawStage();
  api("/api/tools").then((t) => {
    $("tools").textContent = t.ok ? t.tools.map((x) => x.name).join(", ") : "server unreachable: " + t.error;
  });
}

function code(s) { const c = document.createElement("code"); c.textContent = s; return c; }

/* On the static copy the service address can be typed in (kept in this browser); a build can also pin it. */
function serviceRow() {
  if (!RECORDED || window.AIMS_BACKEND) return;
  const row = $("servicerow");
  row.hidden = false;
  $("service").value = BACKEND || "http://127.0.0.1:8080";
  $("servicenote").textContent = STATIC ? "The address of a console service; the default is one running on this computer (see below)." : `Connected to ${BACKEND}.`;
  const rl = RECORDED.config && RECORDED.config.runlocal;
  if (rl && STATIC) {
    $("runlocal").hidden = false;
    $("cmd_docker").textContent = rl.docker || "";
    $("cmd_script").textContent = rl.script || "";
    const a = $("runlocal_repo"); a.href = rl.repo; a.textContent = rl.repo.replace(/^https?:\/\//, "");
    $("cmd_docker").hidden = !rl.docker; $("cmd_script").hidden = !rl.script;
  }
  $("serviceset").onclick = () => {
    const v = $("service").value.trim().replace(/\/$/, "");
    try { if (v) localStorage.setItem("aims-backend", v); else localStorage.removeItem("aims-backend"); } catch (e) {}
    location.reload();
  };
}

function buildModels() {
  const sel = $("modelsel");
  sel.innerHTML = "";
  const models = CFG.models || [{id: CFG.model, provider: "anthropic", label: CFG.model, study: ""}];
  const groups = [["Models of the study", (m) => !/not in the study|in progress/.test(m.study || "")],
                  ["Other", (m) => /not in the study|in progress/.test(m.study || "")]];
  for (const [label, test] of groups) {
    const items = models.filter(test);
    if (!items.length) continue;
    const og = document.createElement("optgroup"); og.label = label;
    for (const m of items) { const o = document.createElement("option"); o.value = m.id; o.textContent = `${m.label} (${m.provider})`; og.appendChild(o); }
    sel.appendChild(og);
  }
  const o = document.createElement("option"); o.value = "__other__"; o.textContent = "Another model id"; sel.appendChild(o);
  sel.value = models.some((m) => m.id === CFG.model) ? CFG.model : "__other__";
  if (sel.value === "__other__") $("model").value = CFG.model;
  sel.onchange = modelChanged;
  $("model").oninput = modelChanged;
  modelChanged();
}

function modelChanged() {
  const sel = $("modelsel"), other = sel.value === "__other__";
  $("modelfree").hidden = !other;
  const m = (CFG.models || []).find((x) => x.id === sel.value);
  $("modelnote").textContent = m ? `${m.label}: ${m.study}.` : "Provider is read from the id (gpt- for OpenAI, otherwise Anthropic).";
  // only the chosen model's provider needs a key; the other field stays filled but out of the way
  const provider = providerOf(chosenModel());
  for (const p of ["anthropic", "openai"]) $("key_" + p).closest(".ctl").hidden = p !== provider;
}

function chosenModel() {
  const sel = $("modelsel");
  return sel.value === "__other__" ? $("model").value.trim() : sel.value;
}

function providerOf(model) {
  const m = (CFG.models || []).find((x) => x.id === model);
  if (m) return m.provider;
  return /^(gpt|o[0-9])/i.test(model) ? "openai" : "anthropic";
}

function buildChain() {
  const chain = $("loops");
  chain.innerHTML = "";
  CFG.loops.forEach((loop, i) => {
    const d = LOOPS[loop] || {n: String(i + 1).padStart(2, "0"), title: loop, cls: "s" + ((i % 3) + 1), text: ""};
    if (i > 0) {
      const a = el("div", "arrow"); a.append("→"); a.appendChild(el("small", "", "hand-off"));
      chain.appendChild(a);
    }
    const b = el("button", "step " + d.cls);
    b.type = "button"; b.dataset.loop = loop; b.setAttribute("aria-pressed", "false");
    const h = el("b"); h.appendChild(el("span", "num", d.n)); h.append(d.title);
    b.appendChild(h);
    b.appendChild(el("span", "", d.text));
    if (CFG.prompt_files && CFG.prompt_files[loop]) b.appendChild(el("small", "", "prompts/" + CFG.prompt_files[loop]));
    b.onclick = () => selectLoop(loop);
    chain.appendChild(b);
  });
}

function selectLoop(loop) {
  currentLoop = loop;
  for (const b of $("loops").querySelectorAll(".step")) b.setAttribute("aria-pressed", b.dataset.loop === loop ? "true" : "false");
  const useSkills = CFG.skills && $("useskills").checked;
  $("prompt").value = ((useSkills ? CFG.prompts : (CFG.prompts_plain || CFG.prompts))[loop]) || "";
  const blocked = CFG.public && loop === "discovery";
  $("start").disabled = !!STATIC || blocked;
  $("start").textContent = blocked ? "Discovery runs with the downloadable console" : "Start run";
  $("promptsrc").textContent = CFG.prompt_files && CFG.prompt_files[loop] ? "prompts/" + CFG.prompt_files[loop] : "";
  const ap = $("approve");
  ap.disabled = loop === "discovery";
  if (loop === "discovery") ap.checked = false;
}

async function refreshRuns() {
  const runs = await api("/api/runs");
  const tb = $("runs");
  tb.innerHTML = "";
  if (!runs.length) {
    const tr = el("tr"); const td = el("td", "empty", "none yet"); td.colSpan = 3; tr.appendChild(td); tb.appendChild(tr);
  }
  for (const r of runs) {
    const tr = el("tr", r.id === currentRun ? "active" : "");
    tr.tabIndex = 0;
    tr.appendChild(el("td", "", r.loop));
    tr.appendChild(el("td", "mono", r.id.slice(0, 15)));
    const td = el("td"); td.appendChild(el("span", "tag " + r.state, r.state.replace("_", " "))); tr.appendChild(td);
    tr.onclick = () => attach(r.id);
    tr.onkeydown = (e) => { if (e.key === "Enter") attach(r.id); };
    tb.appendChild(tr);
  }
  $("runcount").textContent = runs.length ? `${runs.length} run${runs.length === 1 ? "" : "s"} on record` : "";
}

/* ---------- run control ---------- */
$("start").onclick = async () => {
  if (STATIC) return;
  const model = chosenModel();
  if (!model) { note("Choose a model first."); return; }
  const provider = providerOf(model);
  const key = KEYS[provider] || "";
  if ((CFG.byok || BACKEND) && !key) { note(`Enter ${provider === "openai" ? "an OpenAI" : "an Anthropic"} API key for ${model}.`); $("key_" + provider).focus(); return; }
  const body = {loop: currentLoop, prompt: $("prompt").value, model, provider, api_key: key || null,
                max_turns: parseInt($("maxturns").value || "200", 10), approve: $("approve").checked,
                handoff: $("handoff").value.trim() || null, client: CLIENT, use_skills: !!(CFG.skills && $("useskills").checked)};
  $("start").disabled = true;
  const r = await api("/api/runs", {method: "POST", body: JSON.stringify(body)});
  $("start").disabled = false;
  if (r.id) { attach(r.id); refreshRuns(); } else note(r.error || JSON.stringify(r));
};

/* a short message under the Start button, instead of a dialog */
function note(text) {
  let n = $("startnote");
  if (!n) { n = el("p", "note"); n.id = "startnote"; $("start").parentElement.insertAdjacentElement("afterend", n); }
  n.textContent = text;
  n.style.color = "var(--rust)";
}
$("stop").onclick = async () => { if (currentRun) await api(`/api/runs/${currentRun}/stop`, {method: "POST"}); };
$("send").onclick = sendMessage;
$("msg").onkeydown = (e) => { if (e.key === "Enter") sendMessage(); };
async function sendMessage() {
  const text = $("msg").value.trim();
  if (!text || !currentRun) return;
  await api(`/api/runs/${currentRun}/message`, {method: "POST", body: JSON.stringify({text})});
  $("msg").value = "";
}
$("apallow").onclick = () => decide("allow");
$("apdeny").onclick = () => decide("deny");
$("carry").onclick = () => {
  $("handoff").value = lastFinal;
  const i = CFG.loops.indexOf(currentLoop);
  if (i >= 0 && i + 1 < CFG.loops.length) selectLoop(CFG.loops[i + 1]);
  $("settings").scrollIntoView({behavior: "smooth", block: "start"});
};

async function decide(decision) {
  if (!pendingApproval || !currentRun) return;
  await api(`/api/runs/${currentRun}/approve`, {method: "POST", body: JSON.stringify({
    approval_id: pendingApproval, decision, note: $("apnote").value})});
  $("apnote").value = "";
  $("approval").hidden = true;
  pendingApproval = null;
}

/* ---------- event stream ---------- */
function attach(runId) {
  if (ws) { ws.close(); ws = null; }
  for (const t of replayTimers) clearTimeout(t);
  replayTimers = [];
  currentRun = runId;
  ncalls = 0; lastFinal = ""; pendingApproval = null;
  $("approval").hidden = true;
  $("steps").innerHTML = "";
  $("runid").textContent = runId;
  $("download").href = `${SERVICE}/api/runs/${runId}/events.jsonl?client=${encodeURIComponent(CLIENT)}` + (TOKEN ? `&token=${encodeURIComponent(TOKEN)}` : "");
  $("download").hidden = false;
  $("stop").disabled = false;
  for (const id of ["pos", "dist", "move", "vbg", "frame"]) $(id).textContent = "–";
  for (const id of ["unc", "conf", "moves", "vbgs", "frames", "cost"]) $(id).textContent = "";
  $("ncalls").textContent = "0";
  $("final").textContent = "–"; $("final").classList.add("muted");
  $("carry").hidden = true;
  $("latestimg").innerHTML = '<span class="empty">none yet</span>';
  $("imgcap").textContent = "";
  stage.est = []; stage.frame = null; setTemplate(CFG.template); drawStage();
  refreshRuns();
  if (STATIC) {
    $("download").hidden = true;
    $("stop").disabled = true;
    replay(STATIC.events[runId] || []);
    return;
  }
  const base = BACKEND ? new URL(BACKEND) : location;
  const proto = base.protocol === "https:" ? "wss" : "ws";
  // keep the service's path prefix, so a service behind a path (https://example.org/AIMS) works as well as a host
  const prefix = BACKEND ? base.pathname.replace(/\/$/, "") : PAGE_PREFIX;
  ws = new WebSocket(`${proto}://${base.host}${prefix}/ws/runs/${runId}?client=${encodeURIComponent(CLIENT)}` + (TOKEN ? `&token=${encodeURIComponent(TOKEN)}` : ""));
  ws.onmessage = (m) => handle(JSON.parse(m.data));
  ws.onclose = () => {};
}

/* Replay a recorded run at roughly fifteen times its speed, with no pause longer than a second
   and a half (the particle filter took two minutes per estimate on the test machine). */
function replay(events) {
  let at = 0, prev = 0;
  events.forEach((ev, i) => {
    const gap = i === 0 ? 0 : Math.min(1500, Math.max(120, ((ev.t || 0) - prev) * 1000 / 15));
    prev = ev.t || prev;
    at += gap;
    replayTimers.push(setTimeout(() => handle(ev), at));
  });
}

/* one ledger row: kind tag, name, time; then text and/or a mono block */
function addEvent(kind, tag, tagCls, name, t, body, pre, extra) {
  const s = el("div", "ev " + kind);
  const hd = el("div", "ev-h");
  if (tag) hd.appendChild(el("span", "tag " + (tagCls || ""), tag));
  if (name) hd.appendChild(el("span", "name" + (/^[a-z_]+$/.test(name) ? " mono" : ""), name));
  if (extra) hd.appendChild(el("span", "extra", extra));
  if (t !== undefined && t !== null) hd.appendChild(el("span", "t", `t+${t} s`));
  s.appendChild(hd);
  if (body) s.appendChild(el("div", "text", body));
  if (pre) { const p = el("pre"); p.textContent = pre; s.appendChild(p); }
  const steps = $("steps");
  const atBottom = steps.scrollHeight - steps.scrollTop - steps.clientHeight < 80;
  steps.appendChild(s);
  if (atBottom) steps.scrollTop = steps.scrollHeight;
  return s;
}

function setState(state, detail) {
  const s = $("runstate");
  s.textContent = state.replace("_", " ");
  s.className = "tag " + state;
  s.hidden = false;
  $("rundetail").textContent = detail || "";
  if (["finished", "error", "stopped"].includes(state)) { $("stop").disabled = true; refreshRuns(); }
}

/* ---------- instrument state ---------- */
const stage = {est: [], frame: null, view: "template", tpl: null};
/* numbers with a real minus sign, and an explicit plus when asked */
function num(v, digits, plus) {
  const s = Math.abs(v).toFixed(digits);
  return v < 0 ? "−" + s : (plus ? "+" + s : s);
}

function parseState(name, text, input) {
  let m;
  if (name === "predict_position" && (m = text.match(/Position:\s*\(([-\d.]+),\s*([-\d.]+)\)\s*\S*m,\s*Confidence:\s*([\d.]+),\s*Uncertainty:\s*\(([\d.]+),\s*([\d.]+)\)\s*\S*m,\s*Distance:\s*([\d.]+)/))) {
    const e = {x: +m[1], y: +m[2], conf: +m[3], sx: +m[4], sy: +m[5], d: +m[6]};
    $("pos").textContent = `(${num(e.x, 1)}, ${num(e.y, 1)})`;
    $("unc").textContent = `µm, ±(${num(e.sx, 1)}, ${num(e.sy, 1)}) at 1σ`;
    $("dist").textContent = `${num(e.d, 1)} µm`;
    $("conf").textContent = `confidence ${e.conf.toFixed(3)}`;
    stage.est.push(e);
    if (input && input.scan_size_um) stage.frame = +input.scan_size_um;
    drawStage();
  }
  if ((name === "move_tip" || name === "execute_movement") && (m = text.match(/x_steps['"]?:\s*([-\d.]+).*?y_steps['"]?:\s*([-\d.]+)/s))) {
    $("move").textContent = `${num(+m[1], 0, true)}, ${num(+m[2], 0, true)}`;
    $("moves").textContent = "stage steps in x, y";
  }
  if ((name === "bottom_gate_ramp" || name === "bottom_gate_ramp_and_scan") && (m = text.match(/ramped to ([-\d.]+) V|completed_vbg['"]?:\s*\[([^\]]*)\]/))) {
    if (m[1]) { $("vbg").textContent = `${(+m[1]).toFixed(2)} V`; $("vbgs").textContent = "ramped"; }
    else { const pts = m[2].split(",").map((v) => +v).filter((v) => !isNaN(v)); $("vbg").textContent = `${pts.length} points`; $("vbgs").textContent = pts.length ? `${Math.min(...pts).toFixed(2)} to ${Math.max(...pts).toFixed(2)} V` : "swept"; }
  }
  if ((name === "set_scan_frame" || name === "get_scan_frame") && (m = text.match(/size=([\deE.+-]+)x([\deE.+-]+)|frame['"]?:\s*\[([^\]]*)\]/))) {
    let w, h;
    if (m[1]) { w = +m[1] * 1e6; h = +m[2] * 1e6; } else { const f = m[3].split(",").map(Number); w = f[2] * 1e6; h = f[3] * 1e6; }
    $("frame").textContent = `${w.toFixed(2)} × ${h.toFixed(2)} µm`;
    $("frames").textContent = "width × height";
    stage.frame = w; drawStage();
  }
}

/* The stage map: the navigation template with the particle-filter estimates drawn on it, in the
   plotstyle_5 idiom (ticks in, all four sides, serif labels). Two views share one drawing: the whole
   template, and a close-up centred on the target. The particle filter's convention is
   x_um = (px - origin_x) / s, y_um = -(py - origin_y) / s with s = template_height_px / template_height_um. */
function setTemplate(t) {
  if (!t || !t.width_px) return;
  const url = t.url && t.url.startsWith("/") ? SERVICE + t.url : t.url;
  stage.tpl = {url, W: t.width_px, H: t.height_px, ox: t.origin_x_px, oy: t.origin_y_px, s: t.height_px / t.height_um};
}
function templateExtent() {
  const t = stage.tpl;
  return {x0: -t.ox / t.s, x1: (t.W - t.ox) / t.s, y0: -(t.H - t.oy) / t.s, y1: t.oy / t.s};
}
function niceStep(range) {
  for (const c of [10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000]) if (range / c <= 6) return c;
  return 5000;
}
$("stageview").onclick = (e) => {
  const b = e.target.closest("button"); if (!b) return;
  stage.view = b.dataset.view;
  for (const x of $("stageview").children) x.setAttribute("aria-pressed", x === b ? "true" : "false");
  drawStage();
};

function drawStage() {
  const svg = $("stagemap");
  const ns = "http://www.w3.org/2000/svg";
  const mk = (tag, attrs, text) => { const e = document.createElementNS(ns, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (text !== undefined) e.textContent = text; return e; };
  svg.innerHTML = "";
  const ests = stage.est, tpl = stage.tpl;
  const view = tpl && stage.view === "template" ? "template" : "close";
  // extent in µm
  let x0, x1, y0, y1;
  if (view === "template") ({x0, x1, y0, y1} = templateExtent());
  else {
    let span = 100;
    if (ests.length) {
      const far = Math.max(...ests.map((e) => Math.max(Math.abs(e.x) + e.sx, Math.abs(e.y) + e.sy)), (stage.frame || 0) / 2 + 1);
      for (const r of [100, 150, 200, 300, 400, 600, 800, 1000, 1500, 2000, 3000]) { span = r; if (r >= far * 1.15) break; }
    }
    x0 = -span; x1 = span; y0 = -span; y1 = span;
  }
  // plot rectangle: width 250, height from the data aspect
  const L = 56, T = 12, S = 250, PH = Math.round(S * (y1 - y0) / (x1 - x0)), WIDTH = 330, HEIGHT = T + PH + 38;
  svg.setAttribute("viewBox", `0 0 ${WIDTH} ${HEIGHT}`);
  const px = (x) => L + (x - x0) / (x1 - x0) * S;
  const py = (y) => T + (y1 - y) / (y1 - y0) * PH;
  const sx = S / (x1 - x0);                       // svg px per µm
  const defs = mk("defs", {}); const clip = mk("clipPath", {id: "stageclip"}); clip.appendChild(mk("rect", {x: L, y: T, width: S, height: PH})); defs.appendChild(clip); svg.appendChild(defs);
  const g = mk("g", {"clip-path": "url(#stageclip)"}); svg.appendChild(g);
  // the template image, placed by its geometry
  if (tpl) {
    const ex = templateExtent();
    const img = mk("image", {x: px(ex.x0), y: py(ex.y1), width: (ex.x1 - ex.x0) * sx, height: (ex.y1 - ex.y0) * sx, preserveAspectRatio: "none"});
    img.setAttributeNS("http://www.w3.org/1999/xlink", "href", tpl.url); img.setAttribute("href", tpl.url);
    g.appendChild(img);
  } else {
    g.appendChild(mk("line", {x1: px(0), y1: T, x2: px(0), y2: T + PH, stroke: "#E3E4EA", "stroke-width": 1, "stroke-dasharray": "4 3"}));
    g.appendChild(mk("line", {x1: L, y1: py(0), x2: L + S, y2: py(0), stroke: "#E3E4EA", "stroke-width": 1, "stroke-dasharray": "4 3"}));
  }
  // scan frame around the current estimate
  if (ests.length && stage.frame) {
    const e = ests[ests.length - 1], h = stage.frame / 2;
    g.appendChild(mk("rect", {x: px(e.x - h), y: py(e.y + h), width: stage.frame * sx, height: stage.frame * sx, fill: "none", stroke: view === "template" ? "#fff" : "#22222A", "stroke-width": 1.2, "stroke-dasharray": "3 3"}));
  }
  // trail, uncertainty, estimates
  if (ests.length > 1) g.appendChild(mk("polyline", {points: ests.map((e) => `${px(e.x)},${py(e.y)}`).join(" "), fill: "none", stroke: "#3D6B9E", "stroke-width": 1.5}));
  ests.forEach((e, i) => {
    const last = i === ests.length - 1;
    if (last) g.appendChild(mk("ellipse", {cx: px(e.x), cy: py(e.y), rx: Math.max(1, e.sx * sx), ry: Math.max(1, e.sy * sx), fill: "rgba(61,107,158,0.22)", stroke: "#3D6B9E", "stroke-width": 1.5}));
    g.appendChild(mk("circle", {cx: px(e.x), cy: py(e.y), r: last ? 5 : 2.6, fill: last ? "#3D6B9E" : "#fff", stroke: "#22222A", "stroke-width": 1}));
  });
  // the target: a diamond at the origin
  const d = 6, ox = px(0), oy = py(0);
  g.appendChild(mk("polygon", {points: `${ox},${oy - d} ${ox + d},${oy} ${ox},${oy + d} ${ox - d},${oy}`, fill: "#8F3B1E", stroke: "#22222A", "stroke-width": 1}));
  // frame and ticks: all four sides, pointing in
  svg.appendChild(mk("rect", {x: L, y: T, width: S, height: PH, fill: "none", stroke: "#22222A", "stroke-width": 1.2}));
  const fmt = (v) => (Math.abs(v) >= 1000 ? num(v / 1000, v % 1000 ? 1 : 0) + "k" : num(v, 0));
  const step = niceStep(Math.max(x1 - x0, y1 - y0));
  const ticks = (a, b) => { const out = []; for (let v = Math.ceil(a / step) * step; v <= b + 1e-9; v += step) out.push(v); return out; };
  const tick = (x1_, y1_, x2_, y2_, w) => svg.appendChild(mk("line", {x1: x1_, y1: y1_, x2: x2_, y2: y2_, stroke: "#22222A", "stroke-width": w}));
  for (const v of ticks(x0, x1)) {
    const x = px(v); tick(x, T + PH, x, T + PH - 6, 1); tick(x, T, x, T + 6, 1);
    svg.appendChild(mk("text", {x, y: T + PH + 14, "text-anchor": "middle", "font-size": 11.5, fill: "#22222A", "font-family": "inherit"}, fmt(v)));
  }
  for (const v of ticks(y0, y1)) {
    const y = py(v); tick(L, y, L + 6, y, 1); tick(L + S, y, L + S - 6, y, 1);
    svg.appendChild(mk("text", {x: L - 6, y: y + 4, "text-anchor": "end", "font-size": 11.5, fill: "#22222A", "font-family": "inherit"}, fmt(v)));
  }
  for (const v of ticks(x0 - step / 2, x1).map((v) => v + step / 2)) if (v > x0 && v < x1) { const x = px(v); tick(x, T + PH, x, T + PH - 3.5, 0.8); tick(x, T, x, T + 3.5, 0.8); }
  for (const v of ticks(y0 - step / 2, y1).map((v) => v + step / 2)) if (v > y0 && v < y1) { const y = py(v); tick(L, y, L + 3.5, y, 0.8); tick(L + S, y, L + S - 3.5, y, 0.8); }
  svg.appendChild(mk("text", {x: L + S / 2, y: HEIGHT - 4, "text-anchor": "middle", "font-size": 13, fill: "#22222A", "font-family": "inherit"}, "x (µm)"));
  svg.appendChild(mk("text", {x: 14, y: T + PH / 2, "text-anchor": "middle", "font-size": 13, fill: "#22222A", "font-family": "inherit", transform: `rotate(-90 14 ${T + PH / 2})`}, "y (µm)"));
  const note = !ests.length ? "no position estimate yet" : `${ests.length} estimate${ests.length === 1 ? "" : "s"}`;
  const bg = mk("rect", {x: L + S - 6 - note.length * 6.2, y: T + 5, width: note.length * 6.2 + 4, height: 15, fill: "rgba(255,255,255,0.85)"}); if (tpl) svg.appendChild(bg);
  svg.appendChild(mk("text", {x: L + S - 6, y: T + 16, "text-anchor": "end", "font-size": 11.5, fill: "#5A5C6B", "font-family": "inherit"}, note));
  // caption
  const cap = $("stagecap");
  if (view === "template") {
    const ex = templateExtent();
    cap.textContent = `Position estimates on the navigation template (${Math.round(ex.x1 - ex.x0)} × ${Math.round(ex.y1 - ex.y0)} µm, origin at the target). Ellipse: one standard deviation. Dashed square: the scan frame.`;
  } else cap.textContent = "Close-up around the target. Ellipse: one standard deviation. Dashed square: the scan frame.";
}

/* MCP tool replies arrive as {"result": "<json string>"}; unwrap for display. */
function pretty(text) {
  try {
    let v = JSON.parse(text);
    if (v && typeof v.result === "string") { try { v = JSON.parse(v.result); } catch (e) { v = v.result; } }
    if (v && typeof v === "object" && "image" in v) v.image = "[image]";
    return typeof v === "string" ? v : JSON.stringify(v, null, 1);
  } catch (e) { return text; }
}

const lastInput = {};
const SCAN_TOOLS = new Set(["scan", "get_sxm_image", "get_sxm_image_processed", "convert_sxm_to_png", "run_gp_regression", "bottom_gate_ramp_and_scan"]);
function handle(ev) {
  switch (ev.type) {
    case "status": setState(ev.state, ev.detail); break;
    case "system": {
      const tools = (ev.tools || []).length;
      const mcp = (ev.mcp_servers || []).map((s) => `${s.name} ${s.status}`).join(", ");
      addEvent("system", "session", "", ev.subtype, ev.t, null, null, `${tools} tools, ${mcp || "no MCP server"}, ${ev.model || ""}`);
      break;
    }
    case "assistant_text": addEvent("agent", "agent", "navy", "", ev.t, ev.text); break;
    case "tool_call":
      ncalls += 1; $("ncalls").textContent = String(ncalls);
      lastInput[ev.name] = ev.input;
      if (ev.name === "load_template" && ev.input && stage.tpl) {   // the run's own geometry wins over the default
        const i = ev.input;
        if (i.origin_x_px != null) stage.tpl.ox = +i.origin_x_px;
        if (i.origin_y_px != null) stage.tpl.oy = +i.origin_y_px;
        if (i.template_height_um) stage.tpl.s = stage.tpl.H / +i.template_height_um;
        drawStage();
      }
      addEvent("call", "call", "steel", ev.name, ev.t, null, JSON.stringify(ev.input, null, 1));
      break;
    case "tool_result": {
      const shown = pretty(ev.text || "");
      const s = addEvent("result" + (ev.is_error ? " error" : ""), ev.is_error ? "error" : "result", ev.is_error ? "rust" : "slate", ev.name, ev.t, null,
                         shown.length > 1500 ? shown.slice(0, 1500) + " …" : shown);
      for (const src of ev.images || []) {
        const scan = SCAN_TOOLS.has(ev.name);          // a scan covers a square frame whatever its pixel grid
        const img = document.createElement("img"); img.src = src; if (scan) img.className = "scan"; s.appendChild(img);
        $("latestimg").innerHTML = "";
        const big = document.createElement("img"); big.src = src; if (scan) big.className = "scan";
        big.onload = () => {
          if (big.naturalWidth < 300) { big.classList.add("pixels"); img.classList.add("pixels"); }
          const f = stage.frame ? `${stage.frame} × ${stage.frame} µm frame, ` : "";
          $("imgcap").textContent = `${ev.name}: ${big.naturalWidth} × ${big.naturalHeight} pixels` + (scan ? `, ${f}shown square` : "");
        };
        $("latestimg").appendChild(big);
      }
      parseState(ev.name, ev.text || "", lastInput[ev.name]);
      break;
    }
    case "awaiting_approval":
      pendingApproval = ev.approval_id;
      $("apname").textContent = ev.name;
      $("apinput").textContent = JSON.stringify(ev.input, null, 1);
      $("approval").hidden = false;
      break;
    case "approval":
      if (pendingApproval === ev.approval_id) { pendingApproval = null; $("approval").hidden = true; }
      addEvent("operator", "operator", "terra", ev.decision === "allow" ? "allowed" : "denied", ev.t, ev.note || "");
      break;
    case "operator_message": addEvent("operator", "operator", "terra", "message", ev.t, ev.text); break;
    case "result":
      lastFinal = ev.result || "";
      $("final").textContent = lastFinal || "(no final message)";
      $("final").classList.remove("muted");
      $("carry").hidden = !lastFinal;
      $("cost").textContent = ev.total_cost_usd != null ? `$${(+ev.total_cost_usd).toFixed(3)}, ${ev.num_turns} turns, ${(ev.duration_ms / 60000).toFixed(1)}\u00a0min` : "";
      addEvent("end", "end", "fill", ev.subtype, ev.t, ev.result || "", null,
               `${ev.num_turns} turns, ${ev.total_cost_usd != null ? "$" + (+ev.total_cost_usd).toFixed(3) : "cost unknown"}`);
      break;
    case "error": addEvent("result error", "error", "rust", "", ev.t, ev.message); break;
  }
}

init();
