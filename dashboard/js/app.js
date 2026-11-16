/* Core dashboard: navigation, chat (voice in/out), missions, approvals. */

function parseJson(text, fallback = {}) {
  try {
    return JSON.parse(text);
  } catch (err) {
    return fallback;
  }
}

const App = {
  panel: "chat",
  pollTimer: null,
  recognition: null,
  recording: false,

  init() {
    this.bindNav();
    this.bindChat();
    this.bindMissions();
    this.bindApprovals();
    this.setupMic();
    this.refreshHealth();
    setInterval(() => this.refreshHealth(), 15000);
    this.startPolling();
    this.switchPanel("chat");
  },

  switchPanel(name) {
    this.panel = name;
    $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.panel === name));
    $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `panel-${name}`));
    this.loadPanel(name);
  },

  loadPanel(name) {
    const loaders = {
      missions: () => this.refreshMissions(),
      approvals: () => this.refreshApprovals(),
      agents: () => this.refreshAgents(),
      tools: () => this.refreshTools(),
      memory: () => this.refreshMemory(),
      audit: () => this.refreshAudit(),
      settings: () => this.refreshSettings(),
    };
    if (loaders[name]) loaders[name]();
  },

  bindNav() {
    $$(".nav-item").forEach((btn) =>
      btn.addEventListener("click", () => this.switchPanel(btn.dataset.panel)));
  },

  async refreshHealth() {
    const badge = $("#provider-badge");
    try {
      const h = await API.get("/api/v1/health");
      const p = h.provider || {};
      const mode = p.remote_configured && p.mode !== "offline" ? `cloud · ${p.remote_model}` : "offline mode";
      badge.textContent = `${h.status === "ok" ? "●" : "▲"} ${mode}`;
      badge.className = `chip ${h.status === "ok" ? "chip-ok" : "chip-warn"}`;
    } catch (err) {
      badge.textContent = "▲ backend offline";
      badge.className = "chip chip-red";
    }
  },

  /* ---------------- chat ---------------- */

  bindChat() {
    $("#chat-form").addEventListener("submit", (e) => {
      e.preventDefault();
      this.sendChat();
    });
    $("#tts-enabled").addEventListener("change", (e) => {
      if (!e.target.checked && "speechSynthesis" in window) speechSynthesis.cancel();
    });
  },

  appendBubble(role, text, meta) {
    const log = $("#chat-log");
    const div = document.createElement("div");
    div.className = `bubble ${role === "user" ? "bubble-user" : "bubble-bot"}`;
    div.textContent = text;
    if (meta) {
      const m = document.createElement("div");
      m.className = "bubble-meta";
      m.textContent = meta;
      div.appendChild(m);
    }
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
    if (role !== "user" && $("#tts-enabled").checked && "speechSynthesis" in window) {
      const utter = new SpeechSynthesisUtterance(text.replace(/[*_`#>]/g, ""));
      speechSynthesis.speak(utter);
    }
  },

  async sendChat() {
    const input = $("#chat-text");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    this.appendBubble("user", text);
    try {
      const res = await API.post("/api/v1/chat", { message: text });
      this.appendBubble("assistant", res.reply, `${res.provider} · ${res.model}`);
      const notice = $("#chat-notice");
      if (res.notice) {
        notice.textContent = res.notice;
        notice.classList.remove("hidden");
      } else if (res.degraded) {
        notice.textContent = "Offline provider answered — set JARVIS_OPENAI_API_KEY in .env for full AI replies.";
        notice.classList.remove("hidden");
      } else {
        notice.classList.add("hidden");
      }
    } catch (err) {
      toast(err.message, "error");
      this.appendBubble("assistant", `⚠️ ${err.message}`);
    }
  },

  setupMic() {
    const btn = $("#btn-mic");
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      btn.disabled = true;
      btn.title = "Speech recognition is not available in this browser";
      return;
    }
    const rec = new SR();
    rec.lang = navigator.language || "en-US";
    rec.interimResults = false;
    rec.maxAlternatives = 1;
    rec.onresult = (e) => {
      $("#chat-text").value = e.results[0][0].transcript;
      this.sendChat();
    };
    rec.onerror = () => {
      this.setRecording(false);
      toast("Microphone error — check permission", "error");
    };
    rec.onend = () => this.setRecording(false);
    this.recognition = rec;
    const start = (e) => {
      e.preventDefault();
      if (this.recording) return;
      this.setRecording(true);
      try {
        rec.start();
      } catch (err) { /* already started */ }
    };
    const stop = (e) => {
      e.preventDefault();
      if (this.recording) {
        try {
          rec.stop();
        } catch (err) { /* already stopped */ }
      }
    };
    btn.addEventListener("mousedown", start);
    btn.addEventListener("touchstart", start, { passive: false });
    btn.addEventListener("mouseup", stop);
    btn.addEventListener("mouseleave", stop);
    btn.addEventListener("touchend", stop);
  },

  setRecording(on) {
    this.recording = on;
    $("#btn-mic").classList.toggle("recording", on);
  },

  /* ---------------- missions ---------------- */

  bindMissions() {
    $("#mission-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const goal = $("#mission-goal").value.trim();
      if (!goal) return;
      $("#mission-goal").value = "";
      try {
        const report = await API.post("/api/v1/missions", { goal });
        this.renderMissionDetail(report);
        this.refreshMissions();
        const label = String(report.mission.status).replaceAll("_", " ");
        toast(report.mission.status === "completed" ? `Mission completed ✓` : `Mission ${label}`, "ok");
      } catch (err) {
        toast(err.message, "error");
      }
    });
  },

  async refreshMissions() {
    try {
      const missions = await API.get("/api/v1/missions");
      const list = $("#missions-list");
      list.innerHTML = missions.length
        ? missions.map((m) => `
          <div class="card">
            <div class="card-top">
              <span class="card-title">#${m.id} ${esc(m.title)}</span>
              ${statusChip(m.status)}
            </div>
            <div class="card-sub">${esc(m.goal)}</div>
            <div class="chips">${chipHtml(`${m.completed_steps}/${m.step_count} steps`, "chip-accent")}</div>
            <div class="card-actions">
              <button class="btn btn-sm" data-open="${m.id}">Open</button>
            </div>
          </div>`).join("")
        : emptyBox("No missions yet — describe a goal above.");
      $$("[data-open]", list).forEach((b) =>
        b.addEventListener("click", () => this.openMission(Number(b.dataset.open))));
    } catch (err) {
      toast(err.message, "error");
    }
  },

  async openMission(id) {
    try {
      this.renderMissionDetail(await API.get(`/api/v1/missions/${id}`));
    } catch (err) {
      toast(err.message, "error");
    }
  },

  renderMissionDetail(data) {
    const m = data.mission;
    const box = $("#mission-detail");
    box.classList.remove("hidden");
    const steps = (data.steps || []).map((s) => `
      <div class="step ${esc(s.status)}">
        <span class="step-dot"></span>
        <span>${s.seq}. ${esc(s.title)}</span>
        <span class="mono">${esc(s.tool_key)}</span>
        <span class="step-verify">${
          s.verification ? (s.verification.verified === false ? "⚠ unverified" : "✓ verified") : ""
        }</span>
      </div>`).join("");
    const events = (data.events || []).slice(0, 8).map((e) =>
      `<li>${esc(fmtTime(e.created_at))} — ${esc(e.event)}</li>`).join("");
    box.innerHTML = `
      <div class="card">
        <div class="card-top">
          <span class="card-title">#${m.id} ${esc(m.title)}</span>
          ${statusChip(m.status)}
        </div>
        <div class="card-sub">${esc(m.goal)}</div>
        ${m.result_summary ? `<div class="card-sub">✅ ${esc(m.result_summary)}</div>` : ""}
        ${data.stopped_reason ? `<div class="card-sub">⏸ ${esc(data.stopped_reason)}</div>` : ""}
        <div class="steps">${steps || emptyBox("No steps planned yet.")}</div>
        <div class="card-actions">
          <button class="btn btn-sm btn-primary" data-act="run">▶ Run / continue</button>
          <button class="btn btn-sm" data-act="pause">⏸ Pause</button>
          <button class="btn btn-sm" data-act="resume">⏵ Resume</button>
          <button class="btn btn-sm btn-danger" data-act="cancel">✕ Cancel</button>
        </div>
        ${events ? `<h2 class="subhead">Recent events</h2><ul class="card-sub">${events}</ul>` : ""}
      </div>`;
    $$("[data-act]", box).forEach((btn) =>
      btn.addEventListener("click", () => this.missionAction(m.id, btn.dataset.act)));
  },

  async missionAction(id, action) {
    try {
      const report = await API.post(`/api/v1/missions/${id}/${action}`);
      if (report && report.mission) this.renderMissionDetail(report);
      else this.openMission(id);
      this.refreshMissions();
      toast(`Mission ${action} ✓`, "ok");
    } catch (err) {
      toast(err.message, "error");
    }
  },

  /* ---------------- approvals + emergency stop ---------------- */

  bindApprovals() {
    $("#btn-estop").addEventListener("click", async () => {
      if (!confirm("Emergency stop: cancel ALL running missions and pending approvals?")) return;
      try {
        const res = await API.post("/api/v1/emergency-stop");
        toast(`Stopped ${res.missions_cancelled} mission(s)`, "ok");
        this.refreshApprovals();
        this.refreshMissions();
      } catch (err) {
        toast(err.message, "error");
      }
    });
  },

  async refreshApprovals() {
    try {
      const data = await API.get("/api/v1/approvals");
      const badge = $("#approvals-badge");
      badge.textContent = data.pending.length;
      badge.classList.toggle("hidden", data.pending.length === 0);
      const list = $("#approvals-list");
      list.innerHTML = data.pending.length
        ? data.pending.map((a) => `
          <div class="card">
            <div class="card-top">
              <span class="card-title">${esc(a.action)}</span>
              ${tierChip(a.risk_tier)}
              ${statusChip(a.status)}
            </div>
            <div class="card-sub">${esc(a.reason)}</div>
            <pre class="output mono">${esc(pretty(parseJson(a.args_json)))}</pre>
            <div class="card-actions">
              <button class="btn btn-sm btn-primary" data-decide="${a.id}" data-approve="1">✓ Approve</button>
              <button class="btn btn-sm btn-danger" data-decide="${a.id}" data-approve="0">✕ Deny</button>
              <span class="chip chip-muted">expires ${esc(fmtTime(a.expires_at))}</span>
            </div>
          </div>`).join("")
        : emptyBox("Nothing waiting for approval.");
      $$("[data-decide]", list).forEach((b) =>
        b.addEventListener("click", () => this.decideApproval(Number(b.dataset.decide), b.dataset.approve === "1")));
      const recent = $("#approvals-recent");
      recent.innerHTML = data.recent.length
        ? data.recent.map((a) => `
          <div class="card">
            <div class="card-top"><span class="card-title">${esc(a.action)}</span>${statusChip(a.status)}</div>
            <div class="card-sub">${esc(a.tool_key)} · decided ${esc(fmtTime(a.decided_at))} by ${esc(a.decided_by || "—")}</div>
          </div>`).join("")
        : emptyBox("No decisions yet.");
    } catch (err) {
      toast(err.message, "error");
    }
  },

  async decideApproval(id, approved) {
    try {
      await API.post(`/api/v1/approvals/${id}/decide`, { approved });
      toast(approved ? "Approved — mission resumed" : "Denied — action not executed", "ok");
      this.refreshApprovals();
      if (this.panel === "missions") this.refreshMissions();
    } catch (err) {
      toast(err.message, "error");
    }
  },

  /* ---------------- polling ---------------- */

  startPolling() {
    clearInterval(this.pollTimer);
    this.pollTimer = setInterval(async () => {
      try {
        const data = await API.get("/api/v1/approvals");
        const badge = $("#approvals-badge");
        badge.textContent = data.pending.length;
        badge.classList.toggle("hidden", data.pending.length === 0);
        if (this.panel === "approvals") this.refreshApprovals();
        if (this.panel === "missions") this.refreshMissions();
      } catch (err) { /* backend briefly away */ }
    }, 5000);
  },
};
