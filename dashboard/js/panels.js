/* Panels: agents, tools, memory, audit, settings — plus boot. */

Object.assign(App, {
  /* ---------------- agents ---------------- */

  async refreshAgents() {
    try {
      const agents = await API.get("/api/v1/agents");
      $("#agents-list").innerHTML = agents.map((a) => `
        <div class="card">
          <div class="card-top">
            <span class="card-title">${esc(a.name)}</span>
            ${statusChip(a.status)}
          </div>
          <div class="card-sub">${esc(a.description)}</div>
          <div class="chips">${a.tools.length
            ? a.tools.map((t) => tierChip(t.risk_tier) + ` <span class="mono">${esc(t.key)}</span>`).join(" ")
            : chipHtml("no tools yet — planned", "chip-muted")}</div>
        </div>`).join("");
    } catch (err) {
      toast(err.message, "error");
    }
  },

  /* ---------------- tools ---------------- */

  async refreshTools() {
    try {
      const data = await API.get("/api/v1/tools");
      $("#tools-list").innerHTML = data.tools.map((t) => {
        const props = (t.schema && t.schema.properties) || {};
        const required = (t.schema && t.schema.required) || [];
        const sample = {};
        Object.keys(props).forEach((k) => {
          if (required.includes(k)) sample[k] = props[k].type === "integer" || props[k].type === "number" ? 0 : "";
        });
        return `
        <div class="card">
          <div class="card-top">
            <span class="card-title">${esc(t.name)}</span>
            ${tierChip(t.risk_tier)}
            <span class="chip chip-muted mono">v${esc(t.version)}</span>
          </div>
          <div class="card-sub">${esc(t.description)}</div>
          <div class="chips"><span class="mono chip chip-accent">${esc(t.key)}</span></div>
          <details>
            <summary class="card-sub" style="cursor:pointer;margin-top:8px;">Invoke manually</summary>
            <textarea class="mono" id="args-${esc(t.key.replaceAll(".", "-"))}" rows="4"
              style="width:100%;margin-top:8px;">${esc(pretty(sample))}</textarea>
            <div class="card-actions">
              <button class="btn btn-sm btn-primary" data-invoke="${esc(t.key)}">▶ Invoke</button>
            </div>
            <pre class="output mono hidden" id="out-${esc(t.key.replaceAll(".", "-"))}"></pre>
          </details>
        </div>`;
      }).join("");
      $$("[data-invoke]").forEach((b) =>
        b.addEventListener("click", () => this.invokeTool(b.dataset.invoke)));
    } catch (err) {
      toast(err.message, "error");
    }
  },

  async invokeTool(key) {
    const out = $(`#out-${key.replaceAll(".", "-")}`);
    const textarea = $(`#args-${key.replaceAll(".", "-")}`);
    let args;
    try {
      args = JSON.parse(textarea.value || "{}");
    } catch (err) {
      toast("Arguments must be valid JSON", "error");
      return;
    }
    try {
      const res = await API.post(`/api/v1/tools/${key}/invoke`, { args });
      out.classList.remove("hidden");
      if (res.status === "approval_required") {
        out.textContent = "⏸ Approval required — open the Approvals panel to decide.\n\n" + pretty(res.approval);
        toast("Tier 3 action — approval requested", "ok");
      } else {
        out.textContent = pretty(res.result);
        toast(res.result.success ? "Tool executed ✓" : "Tool failed", res.result.success ? "ok" : "error");
      }
    } catch (err) {
      toast(err.message, "error");
    }
  },

  /* ---------------- memory ---------------- */

  async refreshMemory() {
    const q = $("#memory-search").value.trim();
    try {
      const items = await API.get(`/api/v1/memory?q=${encodeURIComponent(q)}&limit=100`);
      $("#memory-list").innerHTML = items.length
        ? items.map((m) => `
          <div class="card">
            <div class="card-top">
              <span class="card-title">${esc(m.kind)}</span>
              <span class="chip chip-muted">${esc(fmtTime(m.created_at))}</span>
              <button class="btn btn-sm btn-danger" data-mem-del="${m.id}">✕</button>
            </div>
            <div>${esc(m.content)}</div>
          </div>`).join("")
        : emptyBox("No memories stored. JARVIS only remembers what you approve.");
      $$("[data-mem-del]").forEach((b) =>
        b.addEventListener("click", async () => {
          try {
            await API.del(`/api/v1/memory/${b.dataset.memDel}`);
            toast("Memory deleted", "ok");
            this.refreshMemory();
          } catch (err) {
            toast(err.message, "error");
          }
        }));
    } catch (err) {
      toast(err.message, "error");
    }
  },

  bindMemory() {
    $("#memory-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const content = $("#memory-content").value.trim();
      if (!content) return;
      try {
        await API.post("/api/v1/memory", { content, kind: $("#memory-kind").value });
        $("#memory-content").value = "";
        toast("Stored in memory", "ok");
        this.refreshMemory();
      } catch (err) {
        toast(err.message, "error");
      }
    });
    let debounce = null;
    $("#memory-search").addEventListener("input", () => {
      clearTimeout(debounce);
      debounce = setTimeout(() => this.refreshMemory(), 250);
    });
    $("#btn-memory-export").addEventListener("click", async () => {
      try {
        const data = await API.get("/api/v1/memory/export");
        const blob = new Blob([pretty(data)], { type: "application/json" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "jarvis-memory-export.json";
        a.click();
        URL.revokeObjectURL(a.href);
      } catch (err) {
        toast(err.message, "error");
      }
    });
    $("#btn-memory-wipe").addEventListener("click", async () => {
      if (!confirm("Delete ALL memories? This cannot be undone.")) return;
      try {
        const res = await API.del("/api/v1/memory");
        toast(`Deleted ${res.deleted} memories`, "ok");
        this.refreshMemory();
      } catch (err) {
        toast(err.message, "error");
      }
    });
  },

  /* ---------------- audit ---------------- */

  async refreshAudit() {
    try {
      const rows = await API.get("/api/v1/audit?limit=150");
      $("#audit-body").innerHTML = rows.length
        ? rows.map((r) => `
          <tr>
            <td>${esc(fmtTime(r.ts))}</td>
            <td>${esc(r.actor)}</td>
            <td class="mono">${esc(r.action)}</td>
            <td class="mono">${esc(r.subject)}</td>
            <td class="outcome-${esc(r.outcome)}">${esc(r.outcome)}</td>
          </tr>`).join("")
        : `<tr><td colspan="5" class="empty">No audit events yet.</td></tr>`;
    } catch (err) {
      toast(err.message, "error");
    }
  },

  /* ---------------- settings ---------------- */

  async refreshSettings() {
    try {
      const [h, u] = await Promise.all([API.get("/api/v1/health"), API.get("/api/v1/usage")]);
      const p = h.provider || {};
      $("#settings-health").innerHTML = `
        <div class="card">
          <div class="card-top"><span class="card-title">System</span>${statusChip(h.status)}</div>
          <div class="card-sub">${esc(h.app)} v${esc(h.version)} · uptime ${esc(Math.round(h.uptime_s))}s · database ${esc(h.database)}</div>
          <div class="chips">
            ${chipHtml(`provider mode: ${esc(p.mode)}`, "chip-accent")}
            ${chipHtml(p.remote_configured ? `remote: ${esc(p.remote_model)}` : "remote: not configured", p.remote_configured ? "chip-ok" : "chip-muted")}
          </div>
        </div>
        <div class="card">
          <div class="card-top"><span class="card-title">Usage today</span>
            ${chipHtml(`$${Number(u.budget.spent_today_usd).toFixed(4)} / $${Number(u.budget.daily_limit_usd).toFixed(2)}`, u.budget.within_budget ? "chip-ok" : "chip-red")}
          </div>
          <div class="card-sub">${u.today.requests || 0} model requests · ${u.today.prompt_tokens || 0} prompt tokens · ${u.today.completion_tokens || 0} completion tokens</div>
          <div class="chips">
            ${chipHtml(`${u.counts.conversations} conversations`, "chip-muted")}
            ${chipHtml(`${u.counts.missions} missions`, "chip-muted")}
            ${chipHtml(`${u.counts.memories} memories`, "chip-muted")}
            ${chipHtml(`${u.counts.audit_events} audit events`, "chip-muted")}
          </div>
        </div>`;
    } catch (err) {
      $("#settings-health").innerHTML = emptyBox(err.message);
    }
  },

  bindSettings() {
    $("#token-form").addEventListener("submit", (e) => {
      e.preventDefault();
      API.setToken($("#token-input").value);
      $("#token-input").value = "";
      toast("Token saved", "ok");
      this.refreshHealth();
      this.loadPanel(this.panel);
    });
  },

  bindPanels() {
    this.bindMemory();
    this.bindSettings();
  },
});

document.addEventListener("DOMContentLoaded", () => {
  App.bindPanels();
  App.init();
});
