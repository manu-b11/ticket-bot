/* ═══════════════════════════════════════════
   chat.js · SupportSync — Netmask Agent
   ═══════════════════════════════════════════ */

const API_BASE = "http://localhost:8000";
const USER_ID = "usr_" + Math.random().toString(36).slice(2, 8);

/* ── State ── */
let lastSender = null;
let lastMsgTime = null;
let isSending = false;
let quickRepliesEl = null;

/* ── Init ── */
document.getElementById("sidebar-user").textContent = USER_ID;

/* ── Helpers ── */
function now() {
  return new Date().toLocaleTimeString("es-CO", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function escapeHtml(t) {
  return t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function scrollToBottom(force = false) {
  const el = document.getElementById("messages");
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  if (force || atBottom) {
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }
}

/* ── Remove quick replies when user interacts ── */
function removeQuickReplies() {
  if (quickRepliesEl) {
    quickRepliesEl.style.opacity = "0";
    quickRepliesEl.style.transform = "translateX(-6px)";
    quickRepliesEl.style.transition = "opacity 0.15s, transform 0.15s";
    setTimeout(() => quickRepliesEl?.remove(), 160);
    quickRepliesEl = null;
  }
}

/* ── Append message ── */
function appendMessage(text, sender, opts = {}) {
  const wrap = document.getElementById("messages");
  const time = opts.time || now();
  const isNewGroup = sender !== lastSender;

  const row = document.createElement("div");
  row.className = "msg-row " + sender;

  /* Avatar — only shown for first in group */
  if (sender === "bot") {
    const av = document.createElement("div");
    av.className = "msg-avatar" + (isNewGroup ? "" : " hidden");
    if (isNewGroup) {
      const img = document.createElement("img");
      img.src = "../assets/netmask.jpg";
      img.alt = "Netmask";
      av.appendChild(img);
    }
    row.appendChild(av);
  }

  /* Bubble */
  const bubble = document.createElement("div");
  bubble.className = "bubble " + sender;

  /* Tail only on first bubble of group */
  if (!isNewGroup) row.classList.add("tail-hidden");

  /* Text content — support basic markdown-ish formatting */
  const formatted = escapeHtml(text)
    .replace(/\n/g, "<br>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(
      /`(.+?)`/g,
      `<code style="font-family:var(--font-mono);font-size:12.5px;background:rgba(0,0,0,0.07);padding:1px 5px;border-radius:4px;">$1</code>`,
    );

  const content = document.createElement("div");
  content.innerHTML = formatted;
  bubble.appendChild(content);

  /* Footer: time + read ticks for user */
  const footer = document.createElement("div");
  footer.className = "bubble-footer";
  footer.innerHTML = `<span class="bubble-time">${time}</span>`;
  if (sender === "user") {
    footer.innerHTML += `<span class="bubble-check" title="Entregado">✓✓</span>`;
  }
  bubble.appendChild(footer);

  row.appendChild(bubble);
  wrap.appendChild(row);

  /* Update state */
  lastSender = sender;
  lastMsgTime = time;

  /* Sidebar preview */
  if (sender === "bot") {
    document.getElementById("sidebar-last").textContent =
      text.length > 38 ? text.slice(0, 38) + "…" : text;
    document.getElementById("sidebar-time").textContent = time;
  }

  scrollToBottom();
  return row;
}

/* ── System notification ── */
function appendSystem(text) {
  const wrap = document.getElementById("messages");
  const el = document.createElement("div");
  el.className = "msg-system";
  el.textContent = text;
  wrap.appendChild(el);
  lastSender = null; // force new group after system msg
  scrollToBottom();
}

/* ── Quick replies ── */
function renderQuickReplies() {
  const wrap = document.getElementById("messages");
  const container = document.createElement("div");
  container.className = "quick-replies-wrap";

  const buttons = [
    { emoji: "🎫", label: "Crear ticket", value: "crear ticket" },
    { emoji: "🔍", label: "Consultar ticket", value: "consultar ticket" },
  ];

  buttons.forEach(({ emoji, label, value }) => {
    const btn = document.createElement("div");
    btn.className = "qa-reply";
    btn.innerHTML = `<span>${emoji}</span><span>${label}</span>`;
    btn.addEventListener("click", () => quickReply(value));
    container.appendChild(btn);
  });

  wrap.appendChild(container);
  quickRepliesEl = container;
  scrollToBottom();
}

function quickReply(text) {
  removeQuickReplies();
  sendMessage(text);
}

/* ── Typing indicator ── */
function showTyping() {
  document.getElementById("typing").style.display = "flex";
  scrollToBottom();
}
function hideTyping() {
  document.getElementById("typing").style.display = "none";
}

/* ── Send ── */
async function sendMessage(customText = null) {
  if (isSending) return;

  const input = document.getElementById("msg-input");
  const text = customText ?? input.value.trim();
  if (!text) return;

  isSending = true;
  input.value = "";
  input.style.height = "auto";
  document.querySelector(".send-btn").disabled = true;

  removeQuickReplies();
  appendMessage(text, "user");
  showTyping();

  try {
    const res = await fetch(`${API_BASE}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: USER_ID, message: text }),
    });

    const data = await res.json();

    /* Slight delay so typing indicator feels natural */
    await new Promise((r) => setTimeout(r, 180));
    hideTyping();
    lastSender = null; // bot breaks user group
    appendMessage(data.response || "Sin respuesta.", "bot");
  } catch {
    await new Promise((r) => setTimeout(r, 400));
    hideTyping();
    lastSender = null;
    appendMessage("❌ Error de conexión. Intenta de nuevo.", "bot");
  } finally {
    isSending = false;
    document.querySelector(".send-btn").disabled = false;
    input.focus();
  }
}

/* ── Polling notifications ── */
async function pollNotifications() {
  try {
    const res = await fetch(`${API_BASE}/notifications/${USER_ID}`);
    const data = await res.json();
    (data.notifications || []).forEach((n) => {
      lastSender = null;
      appendMessage(n.text, "bot", { time: now() });
    });
  } catch {
    /* silencioso */
  }
}

setInterval(pollNotifications, 5000);

/* ── Input auto-resize ── */
const msgInput = document.getElementById("msg-input");
msgInput.addEventListener("input", function () {
  this.style.height = "auto";
  this.style.height = Math.min(this.scrollHeight, 130) + "px";
});

/* ── Enter to send ── */
msgInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

/* ── On load ── */
window.addEventListener("load", () => {
  lastSender = null;
  appendMessage(
    "Hola 👋 Soy el asistente virtual de **Netmask**.\n\nEstoy aquí para ayudarte con la creación y consulta de tickets de soporte.\n\n¿Cómo deseas continuar?",
    "bot",
  );
  renderQuickReplies();
  msgInput.focus();
});
