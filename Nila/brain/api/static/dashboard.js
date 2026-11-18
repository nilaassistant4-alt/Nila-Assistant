"use strict";
// The access token arrives in the URL fragment (never sent to the server, never logged).
// `let`, not `const`: after an idle lock the owner unlocks again here and the
// new session token replaces the old one in memory (never in storage).
let TOKEN = location.hash.slice(1);
history.replaceState(null, "", location.pathname);
const $ = id => document.getElementById(id);
const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
const api = (p, body) => fetch(p, { method: body ? "POST" : "GET", headers: { "Authorization": "Bearer " + TOKEN, "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined })
  .then(async r => {
    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      // keep the WHOLE refusal: `needs` and `how` are what turn a 403 into
      // something the owner can act on instead of a dead end
      const err = new Error(j.error || String(r.status));
      err.status = r.status; err.body = j;
      throw err;
    }
    return j;
  });
// Stopping Nila with ctrl-c leaves the session in the last identity snapshot,
// so the next start makes you type the passphrase again. This is the clean exit.
async function stopNila() {
  if (!confirm("Stop Nila now? The vault is locked and keys are wiped from memory.")) return;
  try { await api("/v1/lock", {}); } catch (e) { /* locked already / unreachable */ }
  document.body.replaceChildren(el("div", "Nila has stopped. You can close this tab. Start Nila again with start-nila.bat.", "m okc"));
}
const CANVAS = [["problem", "Problem"], ["customers", "Customers"], ["value_prop", "Value proposition"], ["solution", "Solution"], ["channels", "Channels"], ["revenue", "Revenue"], ["costs", "Costs"], ["metrics", "Key metric"], ["advantage", "Advantage"]];
const STATUSES = ["idea", "validating", "planning", "building", "launched", "paused", "done"];
let load = 0.2, filesOk = false, CUR = null;

// ---------------------------------------------------------------- chat
function bubble(text, me) { const b = el("div", text, "bub" + (me ? " me" : "")); $("msgs").append(b); $("msgs").scrollTop = 1e9; return b; }
function routeBadge(route) {
  const w = route && route.where ? route.where : "local";
  const cls = route && route.blocked ? "blocked" : (w === "online" ? "online" : "local");
  const label = (w === "online" ? "went online: " : (w === "offline" ? "stayed on this PC: " : "stayed on this PC: ")) + ((route && route.label) || "");
  return el("div", label, "badge " + cls);
}
async function send() {
  const t = $("msg").value.trim(); if (!t) return;
  const mode = document.querySelector("input[name=mode]:checked").value;
  bubble(t, true); $("msg").value = ""; $("send").disabled = true;
  const wait = bubble("thinking…");
  try {
    const r = await api("/v1/chat", { message: t, mode });
    wait.textContent = r.answer || (r.error ? "Problem: " + r.error : "(no reply)");
    if (r.route) { wait.append(routeBadge(r.route)); if (r.route.notice) wait.append(el("div", r.route.notice, "notice")); }
    if (r.error && r.answer) wait.append(el("div", r.error, "notice"));
  } catch (e) { wait.textContent = "Could not reach Nila: " + e.message; }
  $("send").disabled = false; $("msg").focus();
  loadProjects(); tick(); slow();
}
$("send").onclick = send;
$("msg").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
bubble("Hi, I’m Nila. Save an idea, ask me to resume a project, or just talk. Under every answer I show whether it stayed on this PC or went online. Check “What Nila can do” on the right to see what is ready, what needs your permission, and what isn’t built yet.");

// ---------------------------------------------------------------- instructions (#2)
// Standing instructions are owner-only. This panel is the owner's route; the
// model's `remember` tool is refused by the store, and the refusal is shown.
async function loadInstructions() {
  const box = $("instr"); box.replaceChildren();
  try {
    const r = await api("/v1/instructions");
    if (!r.instructions || !r.instructions.length) {
      box.append(el("div", "none yet", "notice")); return;
    }
    for (const it of r.instructions) {
      const row = el("div", null, "instrrow");
      row.append(el("div", it.text, "instrtext"));
      const edit = el("button", "Edit", "mini");
      edit.onclick = () => {
        const next = prompt("Instruction:", it.text);
        if (next == null) return;
        api("/v1/instructions/" + it.id, { text: next })
          .then(loadInstructions)
          .catch(e => { $("ierr").textContent = "refused: " + e.message; });
      };
      const del = el("button", "Remove", "mini");
      del.onclick = () => fetch("/v1/instructions/" + it.id, {
        method: "DELETE", headers: { "Authorization": "Bearer " + TOKEN } })
        .then(loadInstructions);
      row.append(edit, del);
      box.append(row);
    }
  } catch (e) { box.textContent = "instructions unavailable: " + e.message; }
}
async function addInstruction() {
  const t = $("itxt").value.trim(); if (!t) return;
  $("ierr").textContent = "";
  try {
    await api("/v1/instructions", { text: t });
    $("itxt").value = ""; loadInstructions();
  } catch (e) { $("ierr").textContent = e.message; }
}
$("iadd").onclick = addInstruction;
$("itxt").addEventListener("keydown", e => { if (e.key === "Enter") addInstruction(); });

// ---------------------------------------------------------------- idle warning
// 30 minutes without an owner action: say so, and offer one explicit click that
// is a real owner action (POST /v1/session/touch). The 2 s poll below never
// touches the session, so an idle window genuinely runs out.
let warned = false;
function showBanner(text, button) {
  $("bannertext").textContent = text;
  $("bannerok").hidden = !button;
  $("banner").hidden = false;
}
function hideBanner() { $("banner").hidden = true; }
$("bannerok").onclick = async () => {
  try { await api("/v1/session/touch", {}); warned = false; hideBanner(); }
  catch (e) { showBanner("this session has ended - restart Nila and log in again", false); }
};
function idleCheck(sess) {
  if (!sess) return;
  const idle = sess.idle_s || 0;
  if (sess.idle_warning && !warned) {
    warned = true;
    showBanner("No activity for " + Math.round(idle / 60) + " min. Nila locks in " +
               Math.ceil((sess.expires_in_s || 0) / 60) + " min unless you say you are here.", true);
  } else if (sess.idle_warning && warned && !$("banner").hidden) {
    // keep the countdown moving; the server clamps it to the absolute limit
    $("bannertext").textContent = "No activity for " + Math.round(idle / 60) +
      " min. Nila locks in " + Math.ceil((sess.expires_in_s || 0) / 60) + " min unless you say you are here.";
  } else if (!sess.idle_warning && warned && !$("banner").hidden) {
    warned = false; hideBanner();
  }
}
// The moment the vault locks, the poll's 200 turns into a 423. Say what happened
// and offer the way back in: without this, a 60-minute idle lock would mean
// restarting Nila and typing the passphrase at the console.
let reloginShown = false;
function lockedOut(msg) {
  hideBanner();
  const box = el("div", null, "bad");
  box.append(el("div", "session expired or locked (" + msg + ")."));
  if (reloginShown) { $("st").replaceChildren(box); return; }
  reloginShown = true;
  const pass = el("input", null, null); pass.type = "password"; pass.placeholder = "passphrase";
  const totp = el("input", null, null); totp.inputMode = "numeric"; totp.maxLength = 6;
  totp.placeholder = "authenticator code (if you use one)";
  const go = el("button", "Unlock");
  const err = el("div", null, "notice");
  go.onclick = async () => {
    go.disabled = true;
    try {
      const r = await api("/v1/auth/verify", { passphrase: pass.value, totp: totp.value || undefined });
      pass.value = totp.value = "";
      TOKEN = r.session_token; reloginShown = false;
      $("st").replaceChildren(el("div", "Unlocked again (" + r.mode + ").", "m okc"));
      ls(""); tick(); loadProjects(); loadInstructions(); loadConnectors(); slow();
    } catch (e) {
      err.textContent = "Could not unlock: " + e.message;
      go.disabled = false;
    }
  };
  box.append(el("div", "Unlock again here - no restart needed:"), pass, totp, go, err);
  $("st").replaceChildren(box);
}

// ---------------------------------------------------------------- connectors (#3)
// Read-only first: the list is fetched, never polled aggressively, and it can
// never contain a key. Anything that changes state goes through one approval
// (passphrase + authenticator code) that is spent by exactly one action. The
// passphrase is held in this variable for that single request and thrown away:
// it is never stored, never logged, and the browser cannot read a key back.
let CXPENDING = null;
function cxStateClass(r) {
  if (r.ready) return "on";
  if (r.on && !r.has_key && r.needs_key) return "err";
  return "off";
}
function cxRow(r) {
  const row = el("div", null, "cxrow"), head = el("div", null, "cxhead");
  head.append(el("span", r.label, "cxname"));
  head.append(el("span", r.ready ? "ON" : (r.on ? "ON (no key)" : "OFF"), "cxstate " + cxStateClass(r)));
  if (!r.placeholder) {
    const toggle = el("button", r.on ? "Turn off" : "Turn on", "mini");
    toggle.onclick = () => cxIntent("connector:" + r.id + ":" + (r.on ? "off" : "on"),
                                    (r.on ? "Switch " : "Switch on ") + r.label,
                                    () => api("/v1/connectors/switch", { id: r.id, on: !r.on }));
    head.append(toggle);
    if (r.kind === "ai" && r.needs_key) {
      const t = el("button", "Test", "mini");
      t.title = "Send one small fixed prompt and count it against today's budget";
      t.onclick = () => cxIntent("connector:" + r.id + ":test", "Test " + r.label,
                                 () => api("/v1/connectors/test", { id: r.id }));
      head.append(t);
    }
  }
  row.append(head);
  const hosts = (r.allowed_hosts || []).join(", ") ||
    (r.egress === "local_only" ? "this PC only" : "-");
  const key = r.has_key ? "key ending " + (r.key_hint || "????") + " (" + r.key_source + ")"
                        : (r.needs_key ? "no key stored" : "no key needed");
  const use = r.usage && r.usage.budgeted
    ? r.usage.requests + " of " + r.usage.allowed + " requests today"
    : (r.usage ? r.usage.requests + " today" : "");
  row.append(el("div", "hosts: " + hosts + " · " + key + (use ? " · " + use : ""), "cxmeta"));
  if (r.why_not) row.append(el("div", r.why_not, "cxmeta"));
  if (r.detail && r.kind === "integration") row.append(el("div", r.detail, "cxmeta"));
  return row;
}
async function loadConnectors() {
  const box = $("cx"); box.replaceChildren();
  try {
    const st = await api("/v1/connectors");
    box.append(el("div", "signed in as " + st.auth_level +
      (st.can_edit_keys ? " · keys editable"
        : st.has_totp ? " · keys need a login that used your authenticator"
        : " · keys need an authenticator: you have none enrolled, so run \"brain totp\" once"), "notice"));
    st.ai.forEach(r => box.append(cxRow(r)));
    box.append(el("div", "Outside services", "notice"));
    st.integrations.filter(r => !r.placeholder).forEach(r => box.append(cxRow(r)));
    if (!st.integrations.filter(r => !r.placeholder).length) {
      box.append(el("div", "none configured. Services are declared in integrations.json; " +
                          "the model can never invent a URL.", "cxmeta"));
    }
    box.append(el("div", "No payment, banking or card connectors exist in this build.", "cxmeta"));
    return st;
  } catch (e) {
    box.textContent = "connectors unavailable: " + e.message;
    return null;
  }
}
function cxIntent(action, what, run) {
  // One approval, one action. `run` is only called after /authorise succeeds.
  CXPENDING = { action, run };
  $("cxwhat").textContent = what + " — this needs a fresh login check (" + action + ")";
  $("cxerr").textContent = "";
  $("cxgate").hidden = false;
  $("cxpass").value = ""; $("cxtotp").value = ""; $("cxpass").focus();
}
async function cxApprove() {
  if (!CXPENDING) return;
  const pass = $("cxpass").value, totp = $("cxtotp").value.trim();
  $("cxerr").textContent = "";
  if (!pass) { $("cxerr").textContent = "enter your passphrase"; return; }
  try {
    await api("/v1/connectors/authorise", { action: CXPENDING.action, passphrase: pass, totp });
  } catch (e) {
    $("cxerr").textContent = e.message;
    // The passphrase is dropped from the DOM on every outcome, success or not.
    $("cxpass").value = ""; $("cxtotp").value = "";
    return;
  }
  const pending = CXPENDING;
  $("cxpass").value = ""; $("cxtotp").value = ""; $("cxgate").hidden = true;
  CXPENDING = null;
  try { await pending.run(); } catch (e) {
    // The server says WHAT is missing (`needs`) and HOW to get it (`how`).
    // Showing only "a key needs an authenticator-verified session" sent the
    // owner to a code they may never have enrolled.
    const how = (e.body && e.body.how) || [];
    $("cxerr").textContent = e.message + (how.length ? " - " + how.join("; ") : "");
  }
  loadConnectors();
}
$("cxgo").onclick = cxApprove;
$("cxno").onclick = () => { CXPENDING = null; $("cxpass").value = ""; $("cxtotp").value = ""; $("cxgate").hidden = true; };
$("cxpass").addEventListener("keydown", e => { if (e.key === "Enter") $("cxtotp").focus(); });
$("cxtotp").addEventListener("keydown", e => { if (e.key === "Enter") cxApprove(); });

// ---------------------------------------------------------------- projects
async function loadProjects() {
  try {
    const d = await api("/v1/projects"), box = $("plist"); box.replaceChildren();
    if (!d.projects.length) box.append(el("div", "No projects yet. Add an idea below.", "mu"));
    d.projects.forEach(p => {
      const x = el("div", null, "proj" + (p.id === CUR ? " on" : ""));
      x.append(el("div", p.title), el("small", p.status + " · " + p.updated_ago + " · business model " + p.canvas_filled + "/" + p.canvas_total));
      x.onclick = () => showProject(p.id); box.append(x);
    });
    const r = await api("/v1/projects/resume"), rs = $("resume");
    rs.textContent = r.resume ? "Pick up where you left off: " + r.resume.project.title + " (" + r.resume.project.status + ")" : "";
    rs.style.cursor = r.resume ? "pointer" : "default";
    rs.onclick = r.resume ? () => showProject(r.resume.project.id) : null;
  } catch (e) { $("plist").textContent = "projects unavailable: " + e.message; }
}
$("padd").onclick = async () => {
  const t = $("ptitle").value.trim(); if (!t) return;
  try { const r = await api("/v1/projects", { title: t, idea: "" }); $("ptitle").value = ""; CUR = r.project.id; await loadProjects(); showProject(CUR); }
  catch (e) { alert(e.message); }
};
function b64(buf) { let s = ""; const u = new Uint8Array(buf); for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000)); return btoa(s); }
async function post(id, action, body) { await api("/v1/projects/" + encodeURIComponent(id) + "/" + action, body); await showProject(id); loadProjects(); }
async function showProject(id) {
  CUR = id; const box = $("pdetail"); box.replaceChildren();
  let d;
  try { d = await api("/v1/projects/" + encodeURIComponent(id)); } catch (e) { box.textContent = e.message; return; }
  const p = d.project, r = d.report, w = el("div", null, "box");
  w.append(el("b", p.title));
  const sr = el("div", null, "row m"), ss = document.createElement("select");
  STATUSES.forEach(s => { const o = el("option", s); o.value = s; if (s === p.status) o.selected = true; ss.append(o); });
  const sb = el("button", "Set status"); sb.onclick = () => post(id, "status", { status: ss.value }).catch(e => alert(e.message)); sr.append(ss, sb); w.append(sr);
  w.append(el("pre", r.summary), el("b", "Next steps"));
  const ul = el("ul"); r.next_steps.forEach(s => ul.append(el("li", s))); w.append(ul);
  w.append(el("b", "Business model"));
  CANVAS.forEach(([k, label]) => { w.append(el("div", label + ": " + (p.canvas[k] || "—"), p.canvas[k] ? "m" : "m mu")); });
  const cr = el("div", null, "row m"), cs = document.createElement("select"), ci = document.createElement("input");
  CANVAS.forEach(([k, label]) => { const o = el("option", label); o.value = k; cs.append(o); }); ci.placeholder = "fill in this block…";
  const cb = el("button", "Save"); cb.onclick = () => ci.value.trim() && post(id, "canvas", { block: cs.value, text: ci.value }).catch(e => alert(e.message)); cr.append(cs, ci, cb); w.append(cr);
  w.append(el("b", "Milestones"));
  p.milestones.forEach((m, i) => {
    const row = el("div", (m.done ? "✔ " : "○ ") + m.text, "m");
    if (!m.done) { const b = el("button", "Done"); b.onclick = () => post(id, "complete", { index: i + 1 }).catch(e => alert(e.message)); row.append(b); }
    w.append(row);
  });
  const mr = el("div", null, "row m"), mi = document.createElement("input"), mb = el("button", "Add"); mi.placeholder = "next milestone…";
  mb.onclick = () => mi.value.trim() && post(id, "milestone", { text: mi.value }).catch(e => alert(e.message)); mr.append(mi, mb); w.append(mr);
  w.append(el("b", "Notes"));
  p.notes.slice(-5).forEach(n => w.append(el("div", new Date(n.ts).toLocaleDateString() + ": " + n.text, "m mu")));
  const nr = el("div", null, "row m"), ni = document.createElement("input"), nb = el("button", "Add"); ni.placeholder = "progress note…";
  nb.onclick = () => ni.value.trim() && post(id, "note", { text: ni.value }).catch(e => alert(e.message)); nr.append(ni, nb); w.append(nr);
  w.append(el("b", "Files (encrypted)"));
  p.files.forEach(f => {
    const row = el("div", f.name + " · " + Math.round(f.size / 1024) + " KB", "m"), b = el("button", "Save copy");
    b.onclick = async () => {
      try {
        const g = await api("/v1/projects/" + encodeURIComponent(id) + "/files/" + encodeURIComponent(f.id));
        const bin = atob(g.data_b64), u = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
        const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([u])); a.download = g.name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 4000);
      } catch (e) { alert(e.message); }
    };
    row.append(b); w.append(row);
  });
  const fi = document.createElement("input"); fi.type = "file";
  fi.onchange = () => {
    const f = fi.files[0]; if (!f) return;
    if (f.size > 8 * 1024 * 1024) { alert("Files can be up to 8 MB."); return; }
    const rd = new FileReader();
    rd.onload = () => post(id, "files", { name: f.name, data_b64: b64(rd.result) }).catch(e => alert(e.message));
    rd.readAsArrayBuffer(f);
  };
  w.append(fi); box.append(w); loadProjects();
}

// ---------------------------------------------------------------- files (read-only browser)
async function ls(p) {
  try {
    const d = await api("/v1/files?path=" + encodeURIComponent(p || ""));
    filesOk = true; $("path").textContent = d.path || "allowed folders";
    const box = $("files"); box.replaceChildren();
    if (d.parent != null && d.path) { const u = el("div", "\u2b11 .."); u.onclick = () => ls(d.parent); box.append(u); }
    d.entries.forEach(e => { const x = el("div", (e.dir ? "\u25b8 " : "\u00b7 ") + e.name); if (e.dir) x.onclick = () => ls(e.path); box.append(x); });
    if (!(d.roots || []).length) box.append(el("div", "No folders are shared with the dashboard. Add one with: brain config set agent.dashboard_roots \"[\\\"C:/Users/you/Documents\\\"]\"", "note"));
    const miss = (d.roots || []).filter(r => !r.exists).map(r => r.configured);
    if (miss.length) box.append(el("div", "\u26a0 folder not found: " + miss.join(", "), "bad"));
  } catch (e) { filesOk = false; $("path").textContent = "unavailable: " + e.message; }
}

// ---------------------------------------------------------------- system, approvals, audit
function bar(label, v) {
  if (v == null) return null;
  const d = el("div", label + " " + v + "%", "m"), p = document.createElement("progress");
  p.max = 100; p.value = v; d.append(p); return d;
}
function sessionCountdown(raw) {
  const n = Math.max(0, Number(raw) || 0), h = Math.floor(n / 3600);
  const m = Math.floor((n % 3600) / 60), s = n % 60;
  return h ? h + ":" + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0")
           : m + ":" + String(s).padStart(2, "0");
}
async function tick() {
  try {
    const [s, mt] = await Promise.all([api("/v1/status"), api("/v1/metrics")]), m = mt.metrics;
    const cpu = m.cpu_pct != null ? m.cpu_pct : (m.load1 != null ? Math.round(m.load1 / (m.cpus || 1) * 100) : null);
    if (cpu != null) load = Math.min(1, cpu / 100);
    if (!filesOk && s.unlocked && s.session && !s.session.degraded) ls("");
    const st = $("st"); st.replaceChildren();
    st.append(el("div", s.unlocked ? "Vault: unlocked" : "Vault: locked - restart Nila to unlock.", s.unlocked ? "m okc" : "m bad"));
    idleCheck(s.session);
    if (s.session) {
      let expiry = "idle expires in " + sessionCountdown(s.session.expires_in_s);
      if (s.session.absolute_expires_in_s != null)
        expiry += " · maximum " + sessionCountdown(s.session.absolute_expires_in_s);
      st.append(el("div", "Session: " + s.session.mode + (s.session.degraded ? " (limited)" : " (full)") + " · " + expiry, "m"));
    }
    [bar(m.cpu_pct != null ? "CPU" : "Load avg", cpu), bar("Memory", m.mem_pct), bar("Disk", m.disk_pct), bar("Battery" + (m.plugged ? " \u26a1" : ""), m.battery_pct)].forEach(x => x && st.append(x));
    const a = await api("/v1/audit?limit=10"), log = $("log"); log.replaceChildren();
    a.entries.slice().reverse().forEach(e => {
      // show the event and the ONE field that explains it; never the session
      // token or a key hint, which is why this is an allowlist and not a dump
      let why = "";
      if (e.event === "session_idle_lock") why = "idle " + Math.round((e.idle_s || 0) / 60) + " min";
      else if (typeof e.reason === "string") why = e.reason;
      else if (typeof e.provider === "string") why = e.provider;
      else if (typeof e.detail === "string") why = e.detail;
      log.append(el("div", (e.ts ? new Date(e.ts).toLocaleTimeString() + " " : "") +
        (e.event || "event") + (why ? " · " + why : "")));
    });
    if (s.unlocked) {
      // A reminder that came due is the one thing that must not wait for a
      // question: it is checked on every tick. `consume=false` only reports, so
      // the record is closed when the owner presses Done, not by a poll.
      try {
        const due = await api("/v1/reminders/due");
        const db = $("due");
        if (db) db.replaceChildren(...(due.due || []).map(r => {
          const d = el("div", "\u23f0 " + r.title + (r.late_minutes ? " \u00b7 " + r.late_minutes + " min late" : "") + " ", "bad");
          const b = el("button", "Done");
          b.onclick = () => api("/v1/reminders/close", { id: r.id }).then(tick).catch(e => alert(e.message));
          d.append(b); return d;
        }));
      } catch (e) { /* the next tick tries again */ }
      const c = await api("/v1/consent"), cs = $("cs"); cs.replaceChildren();
      if (!c.pending.length) cs.textContent = "none";
      c.pending.forEach(p => {
        const d = el("div", p.label + " · " + p.scope + (p.purpose ? " — " + p.purpose : ""), "m");
        [["Allow once", "affirm"], ["Deny", "deny"]].forEach(([t, k]) => {
          const b = el("button", t); b.onclick = () => api("/v1/consent/" + k, { id: p.id }).then(tick).catch(e => alert(e.message)); d.append(b);
        });
        cs.append(d);
      });
    }
  } catch (e) {
    const expired = /401|expired|unauthor|423|locked/i.test(e.message);
    if (expired) lockedOut(e.message); else $("st").replaceChildren(el("span", "disconnected: " + e.message, "bad"));
  }
}
const STATE_TEXT = { possible: "ready", needs_permission: "asks you first", needs_setup: "needs setup", blocked: "blocked", planned: "not built yet" };
async function slow() {
  try {
    const b = await api("/v1/budget"), bx = $("bud"); bx.replaceChildren();
    if (!b.budgets.length) bx.textContent = "not used";
    b.budgets.forEach(x => {
      const d = el("div", x.provider + ": " + x.used + " of " + x.allowed + " requests today" + (x.cooldown_s ? " · paused " + x.cooldown_s + "s" : ""), "m"), p = document.createElement("progress");
      p.max = 100; p.value = x.pct; d.append(p); bx.append(d);
      if (x.cap_is_default) bx.append(el("div", "Using a safe default limit. Set your real one from your Google AI Studio page.", "note"));
    });
    const c = await api("/v1/capabilities"), cx = $("caps"); cx.replaceChildren();
    c.capabilities.forEach(i => {
      const row = el("div", null, "cap"); row.title = [i.reason, i.how].filter(Boolean).join(" — ");
      row.append(el("span", i.label), el("span", STATE_TEXT[i.state] || i.state, "st-" + i.state)); cx.append(row);
    });
    const h = await api("/v1/diagnostics"), hx = $("hl"); hx.replaceChildren();
    h.health.forEach(i => {
      const row = el("div", i.name + ": " + i.detail, "m " + (i.status === "ok" ? "okc" : i.status === "warn" ? "warnc" : "bad")); if (i.fix) row.title = i.fix; hx.append(row);
    });
  } catch (e) { /* the fast poll already shows a disconnected message */ }
}
$("rep").onclick = async () => {
  const bx = $("repbox");
  if (!bx.hidden) { bx.hidden = true; return; }
  try { const r = await api("/v1/diagnostics?format=text"); bx.textContent = r.text; bx.hidden = false; } catch (e) { alert(e.message); }
};

// ---------------------------------------------------------------- background
const cv = $("c"), g = cv.getContext("2d"); let N = [];
function fit() { cv.width = cv.clientWidth; cv.height = cv.clientHeight; N = Array.from({ length: 70 }, () => ({ x: Math.random() * cv.width, y: Math.random() * cv.height, a: Math.random() * 6.28, p: Math.random() * 6.28 })); }
addEventListener("resize", fit); fit();
(function f(t) {
  g.clearRect(0, 0, cv.width, cv.height); const sp = .3 + load * 2.2;
  N.forEach(n => { n.a += (Math.random() - .5) * .2; n.x = (n.x + Math.cos(n.a) * sp + cv.width) % cv.width; n.y = (n.y + Math.sin(n.a) * sp + cv.height) % cv.height; });
  for (let i = 0; i < N.length; i++) for (let j = i + 1; j < N.length; j++) { const d = Math.hypot(N[i].x - N[j].x, N[i].y - N[j].y); if (d < 110) { g.strokeStyle = "rgba(90,209,255," + ((1 - d / 110) * .45) + ")"; g.beginPath(); g.moveTo(N[i].x, N[i].y); g.lineTo(N[j].x, N[j].y); g.stroke(); } }
  N.forEach(n => { g.fillStyle = "rgba(140,230,255," + (.55 + .45 * Math.sin(t / 400 + n.p)) + ")"; g.beginPath(); g.arc(n.x, n.y, 2.4, 0, 6.28); g.fill(); });
  requestAnimationFrame(f);
})(0);
$("stopnila").onclick = stopNila;
ls(""); tick(); loadProjects(); loadInstructions(); loadConnectors(); slow(); setInterval(tick, 2000); setInterval(slow, 15000);
