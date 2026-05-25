const API_BASE = "http://localhost:8000";
const USER_ID = "user_" + Math.random().toString(36).slice(2, 9);

document.getElementById("sidebar-user").textContent = USER_ID;

function scrollToBottom() {
  const el = document.getElementById("messages");
  el.scrollTop = el.scrollHeight;
}

function escapeHtml(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function appendMessage(text, sender) {
  const wrap = document.getElementById("messages");

  const row = document.createElement("div");
  row.className = "msg-row " + sender;

  const avatar = document.createElement("div");
  avatar.className = "msg-avatar";
  avatar.textContent = sender === "bot" ? "🤖" : "👤";

  const bubble = document.createElement("div");
  bubble.className = "bubble " + sender;
  bubble.innerHTML = escapeHtml(text).replace(/\n/g, "<br>");

  if (sender === "bot") {
    row.appendChild(avatar);
    row.appendChild(bubble);
  } else {
    row.appendChild(bubble);
    row.appendChild(avatar);
  }

  wrap.appendChild(row);
  scrollToBottom();

  document.getElementById("sidebar-last").textContent = text.slice(0, 40);
  document.getElementById("sidebar-time").textContent = "ahora";
}

function renderQuickReplies() {
  const wrap = document.getElementById("messages");

  const container = document.createElement("div");
  container.className = "quick-replies-message";

  container.innerHTML = `
    <div class="wa-reply" onclick="quickReply('crear ticket')">
      🎫 Crear ticket
    </div>
    <div class="wa-reply" onclick="quickReply('consultar ticket')">
      🔍 Consultar ticket
    </div>
  `;

  wrap.appendChild(container);
  scrollToBottom();
}

function quickReply(text) {
  sendMessage(text);
}

async function sendMessage(customText = null) {
  const input = document.getElementById("msg-input");
  const text = customText || input.value.trim();
  if (!text) return;

  input.value = "";

  appendMessage(text, "user");

  document.getElementById("typing").style.display = "flex";

  try {
    const res = await fetch(`${API_BASE}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: USER_ID,
        message: text,
      }),
    });

    const data = await res.json();

    document.getElementById("typing").style.display = "none";

    appendMessage(data.response || "sin respuesta", "bot");
  } catch (err) {
    document.getElementById("typing").style.display = "none";
    appendMessage("Error de conexión", "bot");
  }
}

document.getElementById("msg-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// ── Notificaciones por polling
function appendNotification(text) {
  const wrap = document.getElementById("messages");

  const row = document.createElement("div");
  row.className = "msg-row bot";

  const avatar = document.createElement("div");
  avatar.className = "msg-avatar";
  avatar.textContent = "🤖";

  const bubble = document.createElement("div");
  bubble.className = "bubble bot";
  bubble.innerHTML = escapeHtml(text).replace(/\n/g, "<br>");

  row.appendChild(avatar);
  row.appendChild(bubble);
  wrap.appendChild(row);
  scrollToBottom();
}

async function pollNotifications() {
  try {
    const res = await fetch(`${API_BASE}/notifications/${USER_ID}`);
    const data = await res.json();
    (data.notifications || []).forEach((n) => appendNotification(n.text));
  } catch {
    // silencioso, no interrumpir el chat
  }
}

setInterval(pollNotifications, 5000); // cada 5 segundos

window.onload = () => {
  appendMessage(
    "Hola, soy el asistente virtual de Netmask.\n\nEstoy aquí para ayudarte con la creación y consulta de tickets de soporte.\n\nIndica cómo deseas continuar.",
    "bot",
  );
  renderQuickReplies();
};
