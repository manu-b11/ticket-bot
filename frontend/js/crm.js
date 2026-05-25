const API = "http://localhost:8000";
const AGENT_ID = "crm_agent_" + Math.random().toString(36).slice(2, 7);
const PAGE_SIZE = 15;

let allTickets = [];
let filteredTickets = [];
let currentPage = 0;
let currentTicket = null;

function toast(msg, type = "info") {
  const wrap = document.getElementById("toast-wrap");
  const t = document.createElement("div");
  t.className =
    "toast" + (type === "ok" ? " ok" : type === "danger" ? " danger" : "");
  t.textContent = msg;
  wrap.appendChild(t);
  setTimeout(() => t.remove(), 3500);
}

function formatDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return (
    d.toLocaleDateString("es-CO", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    }) +
    " " +
    d.toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit" })
  );
}

function estadoBadge(estado) {
  const map = {
    Abierto: "badge-abierto",
    "En Proceso": "badge-proceso",
    Cerrado: "badge-cerrado",
  };
  const cls = map[estado] || "badge-abierto";
  return `<span class="badge ${cls}"><span class="badge-dot"></span>${estado}</span>`;
}

function prioBadge(p) {
  const cls =
    p === "Alta" ? "prio-alta" : p === "Media" ? "prio-media" : "prio-baja";
  return `<span class="${cls}">${p}</span>`;
}

async function loadStats() {
  try {
    const total = allTickets.length;
    const abiertos = allTickets.filter((t) => t.estado === "Abierto").length;
    const en_proceso = allTickets.filter(
      (t) => t.estado === "En Proceso",
    ).length;
    const cerrados = allTickets.filter((t) => t.estado === "Cerrado").length;
    const prioridad_alta = allTickets.filter(
      (t) => t.prioridad === "Alta",
    ).length;

    document.getElementById("stat-total").textContent = total;
    document.getElementById("stat-abiertos").textContent = abiertos;
    document.getElementById("stat-proceso").textContent = en_proceso;
    document.getElementById("stat-cerrados").textContent = cerrados;
    document.getElementById("stat-alta").textContent = prioridad_alta;
    document.getElementById("nav-badge-tickets").textContent = total;

    const base = total || 1;
    document.querySelector(".stat-bar-fill.abierto").style.width =
      (abiertos / base) * 100 + "%";
    document.querySelector(".stat-bar-fill.proceso").style.width =
      (en_proceso / base) * 100 + "%";
    document.querySelector(".stat-bar-fill.cerrado").style.width =
      (cerrados / base) * 100 + "%";
    document.querySelector(".stat-bar-fill.alta").style.width =
      (prioridad_alta / base) * 100 + "%";
  } catch {
    toast("No se pudo cargar estadísticas", "danger");
  }
}

async function loadTickets() {
  document.getElementById("tickets-tbody").innerHTML =
    '<tr class="loading-row"><td colspan="6">Cargando tickets…</td></tr>';
  try {
    const r = await fetch(`${API}/tickets?limit=500`);
    allTickets = await r.json();

    document.getElementById("topbar-sub").textContent =
      `${allTickets.length} registros · actualizado ahora`;
    filterTickets();
    await loadStats();
  } catch {
    document.getElementById("tickets-tbody").innerHTML =
      '<tr class="loading-row"><td colspan="6">Error al conectar con el servidor</td></tr>';
    toast("Error de conexión con el backend", "danger");
  }
}

function filterTickets() {
  const q = document.getElementById("search-input").value.toLowerCase();
  const estado = document.getElementById("filter-estado").value;
  const prioridad = document.getElementById("filter-prioridad").value;
  const tipo = document.getElementById("filter-tipo").value;

  filteredTickets = allTickets.filter((t) => {
    const matchQ =
      !q ||
      t.numero_caso?.toLowerCase().includes(q) ||
      t.titulo?.toLowerCase().includes(q) ||
      t.cliente?.toLowerCase().includes(q);
    const matchE = !estado || t.estado === estado;
    const matchP = !prioridad || t.prioridad === prioridad;
    const matchT = !tipo || t.tipo === tipo;
    return matchQ && matchE && matchP && matchT;
  });

  currentPage = 0;
  renderTable();
}

function renderTable() {
  const tbody = document.getElementById("tickets-tbody");
  const start = currentPage * PAGE_SIZE;
  const slice = filteredTickets.slice(start, start + PAGE_SIZE);

  if (allTickets.length === 0) {
    tbody.innerHTML = `
      <tr><td colspan="6">
        <div class="empty-state">
          <div class="icon">🎫</div>
          <p>Aún no hay tickets registrados</p>
        </div>
      </td></tr>`;
    document.getElementById("pagination").style.display = "none";
    return;
  }

  if (filteredTickets.length === 0) {
    tbody.innerHTML = `
      <tr><td colspan="6">
        <div class="empty-state">
          <div class="icon">🔍</div>
          <p>No hay tickets con estos filtros</p>
          <small>Intenta con otros criterios de búsqueda</small>
        </div>
      </td></tr>`;
    document.getElementById("pagination").style.display = "none";
    return;
  }

  tbody.innerHTML = slice
    .map(
      (t) => `
    <tr onclick="openTicket(${t.id})">
      <td><span class="caso-num">${t.numero_caso}</span></td>
      <td>
        <div class="ticket-title">${t.titulo}</div>
        <div class="ticket-client">${t.cliente}</div>
      </td>
      <td><span class="tipo-chip">${t.tipo}</span></td>
      <td>${prioBadge(t.prioridad)}</td>
      <td>${estadoBadge(t.estado)}</td>
      <td class="date-cell">${formatDate(t.created_at)}</td>
    </tr>
  `,
    )
    .join("");

  const pag = document.getElementById("pagination");
  pag.style.display = "flex";
  document.getElementById("pag-info").textContent =
    `${start + 1}–${Math.min(start + PAGE_SIZE, filteredTickets.length)} de ${filteredTickets.length}`;
  document.getElementById("pag-prev").disabled = currentPage === 0;
  document.getElementById("pag-next").disabled =
    start + PAGE_SIZE >= filteredTickets.length;
}

function changePage(dir) {
  currentPage += dir;
  renderTable();
  document.querySelector(".content").scrollTo({ top: 0, behavior: "smooth" });
}

function openTicket(id) {
  const t = allTickets.find((x) => x.id === id);
  if (!t) return;
  currentTicket = t;

  document.getElementById("m-caso").textContent = t.numero_caso;
  document.getElementById("m-titulo").textContent = t.titulo;
  document.getElementById("m-cliente").textContent = t.cliente;
  document.getElementById("m-estado").innerHTML = estadoBadge(t.estado);
  document.getElementById("m-prioridad").innerHTML = prioBadge(t.prioridad);
  document.getElementById("m-tipo").textContent = t.tipo;
  document.getElementById("m-descripcion").textContent = t.descripcion;
  document.getElementById("m-nombre").textContent = t.nombre || "—";
  document.getElementById("m-cargo").textContent = t.cargo || "—";
  document.getElementById("m-contacto").textContent = t.contacto || "—";
  document.getElementById("m-telefono").textContent = t.numero_contacto || "—";
  document.getElementById("m-created").textContent = formatDate(t.created_at);
  document.getElementById("m-updated").textContent = formatDate(t.updated_at);

  const previo = document.getElementById("m-previo-wrap");
  if (t.caso_previo) {
    previo.style.display = "block";
    document.getElementById("m-previo").textContent = t.caso_previo;
  } else {
    previo.style.display = "none";
  }

  document.querySelectorAll(".status-btn:not(.btn-danger)").forEach((b) => {
    b.classList.toggle("active", b.textContent.trim() === t.estado);
  });

  document.getElementById("modal-overlay").classList.add("open");
}

function closeModal() {
  document.getElementById("modal-overlay").classList.remove("open");
  currentTicket = null;
}

function closeModalOutside(e) {
  if (e.target === document.getElementById("modal-overlay")) closeModal();
}

async function updateStatus(newEstado) {
  if (!currentTicket) return;
  try {
    const r = await fetch(`${API}/tickets/${currentTicket.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ estado: newEstado, user_id: AGENT_ID }),
    });
    if (!r.ok) throw new Error();
    const updated = await r.json();
    currentTicket = updated;

    const idx = allTickets.findIndex((t) => t.id === updated.id);
    if (idx !== -1) allTickets[idx] = updated;

    document.getElementById("m-estado").innerHTML = estadoBadge(updated.estado);
    document.querySelectorAll(".status-btn:not(.btn-danger)").forEach((b) => {
      b.classList.toggle("active", b.textContent.trim() === updated.estado);
    });

    filterTickets();
    await loadStats();
    toast(`Estado actualizado a "${newEstado}"`, "ok");
  } catch {
    toast("Error al actualizar el ticket", "danger");
  }
}

async function deleteTicket() {
  if (!currentTicket) return;
  if (
    !confirm(
      `¿Eliminar el ticket ${currentTicket.numero_caso}? Esta acción no se puede deshacer.`,
    )
  )
    return;
  try {
    const r = await fetch(`${API}/tickets/${currentTicket.id}`, {
      method: "DELETE",
    });
    if (!r.ok) throw new Error();
    allTickets = allTickets.filter((t) => t.id !== currentTicket.id);
    closeModal();
    filterTickets();
    await loadStats();
    toast("Ticket eliminado correctamente", "ok");
  } catch {
    toast("Error al eliminar el ticket", "danger");
  }
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeModal();
});

loadTickets();
