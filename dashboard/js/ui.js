/* JARVIS X UI helpers: DOM shortcuts, escaping, toasts, chips. */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "\u0026amp;")
    .replace(/</g, "\u0026lt;")
    .replace(/>/g, "\u0026gt;")
    .replace(/"/g, "\u0026quot;")
    .replace(/'/g, "\u0026#39;");
}

let toastTimer = null;
function toast(message, kind = "") {
  const el = $("#toast");
  el.textContent = message;
  el.className = `toast ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 3600);
}

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function chipHtml(text, cls = "chip-muted") {
  return `<span class="chip ${cls}">${esc(text)}</span>`;
}

const STATUS_CLASS = {
  completed: "chip-ok", ok: "chip-ok", approved: "chip-ok", ready: "chip-ok",
  running: "chip-accent", verifying: "chip-accent",
  planned: "chip-muted", created: "chip-muted", pending: "chip-muted", builtin: "chip-ok",
  awaiting_approval: "chip-warn", needs_user_input: "chip-warn", paused: "chip-warn",
  reviewed: "chip-warn",
  failed: "chip-red", cancelled: "chip-red", denied: "chip-red", expired: "chip-red",
  untrusted: "chip-red",
};

function statusChip(status) {
  return chipHtml(String(status || "unknown").replaceAll("_", " "), STATUS_CLASS[status] || "chip-muted");
}

function tierChip(tier) {
  const map = {
    1: ["T1 · read", "chip-ok"],
    2: ["T2 · reversible", "chip-warn"],
    3: ["T3 · sensitive", "chip-red"],
  };
  const [label, cls] = map[tier] || [`T${tier}`, "chip-muted"];
  return chipHtml(label, cls);
}

function emptyBox(text) {
  return `<div class="empty">${esc(text)}</div>`;
}

function pretty(value) {
  try {
    return JSON.stringify(value, null, 2);
  } catch (err) {
    return String(value);
  }
}
