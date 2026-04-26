/* ── State ────────────────────────────────────────────────────────────────── */
const state = {
  sessionId: null,
  pairs:     [],   // { id, ynab, bank, score, amountDiff, status, decision, editData }
  ynabPool:  [],   // { id, ynab, decision }
  bankPool:  [],   // { id, bank, decision, createData }
  deferred:  [],   // { ynab } — auto-skipped, counted in stats only
  stats:     {},
  txPreview: null,
  txFilter:  "all",
};

/* ── Utilities ────────────────────────────────────────────────────────────── */
function uid() { return Math.random().toString(36).slice(2, 9); }

function fmtEur(amount) {
  const sign = amount >= 0 ? "+" : "";
  return `${sign}${amount.toFixed(2).replace(".", ",")} €`;
}
function amountClass(v) { return v < 0 ? "neg" : "pos"; }
function esc(s) {
  if (!s) return "";
  return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}
function parseLocalAmount(s) {
  if (!s) return null;
  s = s.trim().replace("€","").replace("EUR","").replace("+","").trim();
  const neg = s.startsWith("-");
  s = s.replace("-","").trim();
  if (s.includes(",") && s.includes("."))
    s = s.lastIndexOf(",") > s.lastIndexOf(".") ? s.replace(/\./g,"").replace(",",".") : s.replace(/,/g,"");
  else if (s.includes(",")) s = s.replace(",",".");
  const v = parseFloat(s);
  return isNaN(v) ? null : (neg ? -v : v);
}
function formatDateDE(iso) { const [y,m,d] = iso.split("-"); return `${d}.${m}.${y}`; }
function parseDateToISO(de) {
  const p = de.split(".");
  if (p.length !== 3) return null;
  const fy = p[2].length === 2 ? "20"+p[2] : p[2];
  const iso = `${fy}-${p[1].padStart(2,"0")}-${p[0].padStart(2,"0")}`;
  return isNaN(new Date(iso)) ? null : iso;
}

/* ── View / error helpers ─────────────────────────────────────────────────── */
function showView(id) {
  document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
  document.getElementById(`view-${id}`).classList.add("active");
}
function showError(elId, msg) {
  const el = document.getElementById(elId);
  el.textContent = msg; el.classList.remove("hidden");
}
function clearError(elId) { document.getElementById(elId).classList.add("hidden"); }

/* ── Modal helpers ────────────────────────────────────────────────────────── */
function showModal(id) {
  document.querySelectorAll(".modal").forEach(m => m.classList.add("hidden"));
  document.getElementById(id).classList.remove("hidden");
  document.getElementById("modal-overlay").classList.remove("hidden");
}
function closeModal() {
  document.getElementById("modal-overlay").classList.add("hidden");
  document.querySelectorAll(".modal").forEach(m => m.classList.add("hidden"));
}
document.getElementById("modal-overlay").addEventListener("click", e => {
  if (e.target === document.getElementById("modal-overlay")) closeModal();
});

/* ═══════════════════════════════════════════════════════════════════════════
   SETUP VIEW
   ═══════════════════════════════════════════════════════════════════════════ */
const budgetSelect  = document.getElementById("budget-select");
const accountSelect = document.getElementById("account-select");
const csvInput      = document.getElementById("csv-input");
const fileLabel     = document.getElementById("file-label");
const fileDrop      = document.getElementById("file-drop");
const startBtn      = document.getElementById("start-btn");

async function loadBudgets() {
  try {
    const res  = await fetch("/api/budgets");
    const data = await res.json();
    if (data.error) { showError("setup-error", data.error); return; }
    budgetSelect.innerHTML = "";
    data.budgets.forEach(b => {
      const opt = document.createElement("option");
      opt.value = b.id; opt.textContent = b.name;
      budgetSelect.appendChild(opt);
    });
    budgetSelect.disabled = false;
    await loadAccounts();
  } catch (e) {
    showError("setup-error", `Verbindungsfehler: ${e.message}`);
  }
}

async function loadAccounts() {
  const budgetId = budgetSelect.value;
  if (!budgetId) return;
  accountSelect.disabled = true;
  accountSelect.innerHTML = "<option>Lade…</option>";
  hideTxPreview();
  const res  = await fetch(`/api/accounts?budget_id=${encodeURIComponent(budgetId)}`);
  const data = await res.json();
  accountSelect.innerHTML = "";
  if (!data.accounts || !data.accounts.length) {
    accountSelect.innerHTML = "<option value=''>Keine offenen Transaktionen</option>";
    startBtn.disabled = true; return;
  }
  data.accounts.forEach(a => {
    const opt = document.createElement("option");
    opt.value = a.id;
    opt.textContent = `${a.name}  (${fmtEur(a.uncleared_balance)} offen)`;
    accountSelect.appendChild(opt);
  });
  accountSelect.disabled = false;
  updateStartBtn();
  await loadTxPreview();
}

budgetSelect.addEventListener("change", loadAccounts);
accountSelect.addEventListener("change", async () => { updateStartBtn(); await loadTxPreview(); });

csvInput.addEventListener("change", () => {
  if (csvInput.files[0]) { fileLabel.textContent = csvInput.files[0].name; updateStartBtn(); }
});
fileDrop.addEventListener("dragover",  e => { e.preventDefault(); fileDrop.classList.add("drag-over"); });
fileDrop.addEventListener("dragleave", () => fileDrop.classList.remove("drag-over"));
fileDrop.addEventListener("drop", e => {
  e.preventDefault(); fileDrop.classList.remove("drag-over");
  const file = e.dataTransfer.files[0];
  if (file) {
    const dt = new DataTransfer(); dt.items.add(file);
    csvInput.files = dt.files; fileLabel.textContent = file.name; updateStartBtn();
  }
});
function updateStartBtn() { startBtn.disabled = !(accountSelect.value && csvInput.files[0]); }

startBtn.addEventListener("click", async () => {
  clearError("setup-error");
  startBtn.disabled = true;
  startBtn.innerHTML = '<span class="spinner"></span> Lade…';
  const fd = new FormData();
  fd.append("budget_id",  budgetSelect.value);
  fd.append("account_id", accountSelect.value);
  fd.append("csv_file",   csvInput.files[0]);
  try {
    const res  = await fetch("/api/session/start", { method: "POST", body: fd });
    const data = await res.json();
    if (data.error) { showError("setup-error", data.error); return; }
    state.sessionId = data.session_id;
    state.stats     = data.stats;
    buildMatchingState(data.matches);
    renderMatchingView();
    showView("matching");
  } catch (e) {
    showError("setup-error", `Netzwerkfehler: ${e.message}`);
  } finally {
    startBtn.disabled = false; startBtn.textContent = "Abgleich starten";
  }
});

/* ── Transaction preview ──────────────────────────────────────────────────── */
function hideTxPreview() {
  state.txPreview = null;
  document.getElementById("tx-preview").classList.add("hidden");
}
async function loadTxPreview() {
  const budgetId  = budgetSelect.value;
  const accountId = accountSelect.value;
  if (!budgetId || !accountId) { hideTxPreview(); return; }
  const preview = document.getElementById("tx-preview");
  const list    = document.getElementById("tx-preview-list");
  preview.classList.remove("hidden");
  list.innerHTML = '<p class="tx-loading"><span class="spinner"></span></p>';
  try {
    const res  = await fetch(`/api/transactions?budget_id=${encodeURIComponent(budgetId)}&account_id=${encodeURIComponent(accountId)}`);
    const data = await res.json();
    if (data.error) { list.innerHTML = `<p class="tx-loading">${data.error}</p>`; return; }
    state.txPreview = data.transactions;
    document.getElementById("count-all").textContent       = data.counts.all;
    document.getElementById("count-cleared").textContent   = data.counts.cleared;
    document.getElementById("count-uncleared").textContent = data.counts.uncleared;
    renderTxPreview(state.txFilter);
  } catch (e) {
    list.innerHTML = `<p class="tx-loading">Ladefehler: ${e.message}</p>`;
  }
}
function renderTxPreview(filter) {
  state.txFilter = filter;
  const txs = state.txPreview?.[filter] ?? [];
  document.querySelectorAll(".filter-tab").forEach(btn =>
    btn.classList.toggle("active", btn.dataset.filter === filter));
  const list = document.getElementById("tx-preview-list");
  if (!txs.length) { list.innerHTML = '<p class="tx-loading">Keine Transaktionen.</p>'; return; }
  list.innerHTML = txs.map(t => `
    <div class="tx-row">
      <span class="tx-date">${t.date}</span>
      <span class="tx-amount ${amountClass(t.amount)}">${fmtEur(t.amount)}</span>
      <span class="tx-payee" title="${esc(t.payee_name)}">${esc(t.payee_name)||"—"}</span>
      <span class="tx-status ${t.cleared}">${t.cleared === "cleared" ? "Gecleared" : "Offen"}</span>
    </div>`).join("");
}
document.querySelectorAll(".filter-tab").forEach(btn =>
  btn.addEventListener("click", () => state.txPreview && renderTxPreview(btn.dataset.filter)));

/* ═══════════════════════════════════════════════════════════════════════════
   MATCH VIEW — two-column UI
   ═══════════════════════════════════════════════════════════════════════════ */

function buildMatchingState(apiMatches) {
  state.pairs = []; state.ynabPool = []; state.bankPool = []; state.deferred = [];
  apiMatches.forEach(m => {
    if (m.type === "matched")
      state.pairs.push({ id: uid(), ynab: m.ynab, bank: m.bank, score: m.score,
                         amountDiff: m.amount_diff, status: "auto", decision: null, editData: null });
    else if (m.type === "ynab_only")
      state.ynabPool.push({ id: uid(), ynab: m.ynab, decision: null });
    else if (m.type === "bank_only")
      state.bankPool.push({ id: uid(), bank: m.bank, decision: null, createData: null });
    else if (m.type === "ynab_deferred")
      state.deferred.push({ ynab: m.ynab });
  });
}

function renderMatchingView() {
  const s = state.stats;
  document.getElementById("match-stats").innerHTML =
    `<span class="badge green">${s.matched} Paare</span>` +
    (s.ynab_only ? `<span class="badge yellow">${s.ynab_only} nur YNAB</span>` : "") +
    (s.bank_only ? `<span class="badge red">${s.bank_only} nur Bank</span>` : "") +
    (s.deferred  ? `<span class="badge dim">${s.deferred} Dauerbucher</span>` : "");
  renderPairs();
  renderYnabPool();
  renderBankPool();
}

/* ── Pairs ────────────────────────────────────────────────────────────────── */
function renderPairs() {
  const list  = document.getElementById("pairs-list");
  const empty = document.getElementById("pairs-empty");
  document.getElementById("pairs-count").textContent = `${state.pairs.length} Paare`;
  if (!state.pairs.length) { list.innerHTML = ""; empty.classList.remove("hidden"); return; }
  empty.classList.add("hidden");
  list.innerHTML = state.pairs.map(buildPairRowHtml).join("");
  list.querySelectorAll(".unmatch-btn").forEach(btn =>
    btn.addEventListener("click", () => unmatchPair(btn.dataset.pairId)));
  list.querySelectorAll(".confirm-pair-btn").forEach(btn =>
    btn.addEventListener("click", () => decidePair(btn.dataset.pairId, "confirm")));
  list.querySelectorAll(".edit-pair-btn").forEach(btn =>
    btn.addEventListener("click", () => openEditPairModal(btn.dataset.pairId)));
  list.querySelectorAll(".skip-pair-btn").forEach(btn =>
    btn.addEventListener("click", () => decidePair(btn.dataset.pairId, "skip")));
  list.querySelectorAll(".undo-pair-btn").forEach(btn =>
    btn.addEventListener("click", () => undoPair(btn.dataset.pairId)));
}

function buildPairRowHtml(pair) {
  const scoreClass = pair.status === "manual" ? "manual" :
    pair.score >= 0.85 ? "high" : pair.score >= 0.60 ? "medium" : "low";
  const scoreText = pair.status === "manual" ? "manuell" : `${Math.round(pair.score * 100)}%`;
  const diffHtml  = pair.amountDiff > 0.01
    ? `<span class="amount-diff-tag">⚠️ ${pair.amountDiff.toFixed(2).replace(".",",")} € Diff</span>` : "";

  let decClass = "", footer = "";
  if (pair.decision === "confirm") {
    decClass = "decision-confirm";
    footer   = `${diffHtml}<span class="pair-decision-tag confirm">✅ Wird gecleared</span>
                <button class="btn ghost undo-pair-btn" data-pair-id="${pair.id}" title="Rückgängig">↩</button>`;
  } else if (pair.decision === "update") {
    decClass = "decision-update";
    footer   = `<span class="pair-decision-tag update">✏️ Angepasst &amp; gecleared</span>
                <button class="btn ghost undo-pair-btn" data-pair-id="${pair.id}" title="Rückgängig">↩</button>`;
  } else if (pair.decision === "skip") {
    decClass = "decision-skip";
    footer   = `<span class="pair-decision-tag skip">⏭ Übersprungen</span>
                <button class="btn ghost undo-pair-btn" data-pair-id="${pair.id}" title="Rückgängig">↩</button>`;
  } else {
    footer = `${diffHtml}
      <div class="pair-actions">
        <button class="btn ghost confirm-pair-btn" data-pair-id="${pair.id}">✅ Bestätigen</button>
        <button class="btn ghost edit-pair-btn"    data-pair-id="${pair.id}">✏️ Bearbeiten</button>
        <button class="btn ghost skip-pair-btn"    data-pair-id="${pair.id}">⏭ Skip</button>
      </div>`;
  }

  return `<div class="pair-row ${decClass}" data-pair-id="${pair.id}">
    <div class="pair-sides">
      <div class="pair-half ynab-half">
        <div class="half-meta">
          <span class="half-date">${pair.ynab.date}</span>
          <span class="half-amount ${amountClass(pair.ynab.amount)}">${fmtEur(pair.ynab.amount)}</span>
        </div>
        <div class="half-payee" title="${esc(pair.ynab.payee_name)}">${esc(pair.ynab.payee_name)||"—"}</div>
        ${pair.ynab.memo ? `<div class="half-memo">${esc(pair.ynab.memo)}</div>` : ""}
      </div>
      <div class="pair-connector">
        <span class="score-pill ${scoreClass}">${scoreText}</span>
        <button class="unmatch-btn" data-pair-id="${pair.id}" title="Trennen">✕</button>
      </div>
      <div class="pair-half bank-half">
        <div class="half-meta">
          <span class="half-date">${pair.bank.date}</span>
          <span class="half-amount ${amountClass(pair.bank.amount)}">${fmtEur(pair.bank.amount)}</span>
        </div>
        <div class="half-payee" title="${esc(pair.bank.payee)}">${esc(pair.bank.payee)||"—"}</div>
        ${pair.bank.memo ? `<div class="half-memo">${esc(pair.bank.memo)}</div>` : ""}
      </div>
    </div>
    <div class="pair-footer">${footer}</div>
  </div>`;
}

function decidePair(pairId, decision) {
  const p = state.pairs.find(p => p.id === pairId);
  if (p) { p.decision = decision; renderPairs(); }
}
function undoPair(pairId) {
  const p = state.pairs.find(p => p.id === pairId);
  if (p) { p.decision = null; p.editData = null; renderPairs(); }
}
function unmatchPair(pairId) {
  const idx = state.pairs.findIndex(p => p.id === pairId);
  if (idx === -1) return;
  const pair = state.pairs.splice(idx, 1)[0];
  state.ynabPool.push({ id: uid(), ynab: pair.ynab, decision: null });
  state.bankPool.push({ id: uid(), bank: pair.bank, decision: null, createData: null });
  renderPairs(); renderYnabPool(); renderBankPool();
}

document.getElementById("confirm-all-btn").addEventListener("click", () => {
  state.pairs.forEach(p => {
    if (p.decision === null && p.score >= 0.85 && p.amountDiff <= 0.01) p.decision = "confirm";
  });
  renderPairs();
});

/* ── YNAB pool ────────────────────────────────────────────────────────────── */
function renderYnabPool() {
  const list  = document.getElementById("ynab-pool-list");
  const empty = document.getElementById("ynab-pool-empty");
  document.getElementById("ynab-pool-count").textContent = `${state.ynabPool.length}`;
  if (!state.ynabPool.length) { list.innerHTML = ""; empty.classList.remove("hidden"); return; }
  empty.classList.add("hidden");
  list.innerHTML = state.ynabPool.map(buildYnabPoolCardHtml).join("");
  list.querySelectorAll(".pool-skip-btn[data-side='ynab']").forEach(btn =>
    btn.addEventListener("click", () => decidePool("ynab", btn.dataset.id, "skip")));
  list.querySelectorAll(".pool-clear-btn").forEach(btn =>
    btn.addEventListener("click", () => decidePool("ynab", btn.dataset.id, "clear")));
  list.querySelectorAll(".pool-defer-btn").forEach(btn =>
    btn.addEventListener("click", () => decidePool("ynab", btn.dataset.id, "defer")));
  list.querySelectorAll(".pool-undo-btn[data-side='ynab']").forEach(btn =>
    btn.addEventListener("click", () => decidePool("ynab", btn.dataset.id, null)));
  list.querySelectorAll(".pool-card[data-side='ynab']:not(.decided)").forEach(setupDrag);
}

function buildYnabPoolCardHtml(item) {
  const decided = item.decision !== null;
  const body = decided
    ? `<div class="pool-decision-tag ${item.decision}">${
        {skip:"⏭ Übersprungen", clear:"✅ Wird gecleared", defer:"🔄 Dauerbucher"}[item.decision]||item.decision
      }</div>
      <button class="btn ghost pool-undo-btn" data-id="${item.id}" data-side="ynab">↩</button>`
    : `<div class="pool-card-actions">
        <button class="btn ghost pool-skip-btn"  data-id="${item.id}" data-side="ynab">⏭ Skip</button>
        <button class="btn ghost pool-clear-btn" data-id="${item.id}">✅ Clearen</button>
        <button class="btn danger pool-defer-btn" data-id="${item.id}">🔄 Dauerbucher</button>
      </div>
      <div class="drag-hint">Ziehe auf Bank-Transaktion zum Zuordnen</div>`;
  return `<div class="pool-card${decided?" decided":""}" draggable="${!decided}"
               data-id="${item.id}" data-side="ynab">
    <div class="half-meta">
      <span class="half-date">${item.ynab.date}</span>
      <span class="half-amount ${amountClass(item.ynab.amount)}">${fmtEur(item.ynab.amount)}</span>
    </div>
    <div class="half-payee" title="${esc(item.ynab.payee_name)}">${esc(item.ynab.payee_name)||"—"}</div>
    ${item.ynab.memo ? `<div class="half-memo">${esc(item.ynab.memo)}</div>` : ""}
    ${body}
  </div>`;
}

/* ── Bank pool ────────────────────────────────────────────────────────────── */
function renderBankPool() {
  const list  = document.getElementById("bank-pool-list");
  const empty = document.getElementById("bank-pool-empty");
  document.getElementById("bank-pool-count").textContent = `${state.bankPool.length}`;
  if (!state.bankPool.length) { list.innerHTML = ""; empty.classList.remove("hidden"); return; }
  empty.classList.add("hidden");
  list.innerHTML = state.bankPool.map(buildBankPoolCardHtml).join("");
  list.querySelectorAll(".pool-skip-btn[data-side='bank']").forEach(btn =>
    btn.addEventListener("click", () => decidePool("bank", btn.dataset.id, "skip")));
  list.querySelectorAll(".pool-create-btn").forEach(btn =>
    btn.addEventListener("click", () => openCreateModalFromPool(btn.dataset.id)));
  list.querySelectorAll(".pool-undo-btn[data-side='bank']").forEach(btn =>
    btn.addEventListener("click", () => decidePool("bank", btn.dataset.id, null)));
  list.querySelectorAll(".pool-card[data-side='bank']:not(.decided)").forEach(setupDrag);
}

function buildBankPoolCardHtml(item) {
  const decided = item.decision !== null;
  const body = decided
    ? `<div class="pool-decision-tag ${item.decision}">${
        {skip:"⏭ Übersprungen", create:"➕ Wird angelegt"}[item.decision]||item.decision
      }</div>
      <button class="btn ghost pool-undo-btn" data-id="${item.id}" data-side="bank">↩</button>`
    : `<div class="pool-card-actions">
        <button class="btn ghost pool-skip-btn"    data-id="${item.id}" data-side="bank">⏭ Skip</button>
        <button class="btn ghost pool-create-btn"  data-id="${item.id}">➕ In YNAB anlegen</button>
      </div>
      <div class="drag-hint">Ziehe auf YNAB-Transaktion zum Zuordnen</div>`;
  return `<div class="pool-card${decided?" decided":""}" draggable="${!decided}"
               data-id="${item.id}" data-side="bank">
    <div class="half-meta">
      <span class="half-date">${item.bank.date}</span>
      <span class="half-amount ${amountClass(item.bank.amount)}">${fmtEur(item.bank.amount)}</span>
    </div>
    <div class="half-payee" title="${esc(item.bank.payee)}">${esc(item.bank.payee)||"—"}</div>
    ${item.bank.memo ? `<div class="half-memo">${esc(item.bank.memo)}</div>` : ""}
    ${body}
  </div>`;
}

function decidePool(side, itemId, decision) {
  const pool = side === "ynab" ? state.ynabPool : state.bankPool;
  const item = pool.find(i => i.id === itemId);
  if (!item) return;
  item.decision = decision;
  if (decision === null && item.createData !== undefined) item.createData = null;
  side === "ynab" ? renderYnabPool() : renderBankPool();
}

/* ── Drag and drop ────────────────────────────────────────────────────────── */
let _drag = null; // { id, side }

function setupDrag(card) {
  card.addEventListener("dragstart", e => {
    _drag = { id: card.dataset.id, side: card.dataset.side };
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", card.dataset.id);
    requestAnimationFrame(() => card.classList.add("dragging"));
    const opp = _drag.side === "ynab" ? "bank" : "ynab";
    document.querySelectorAll(`.pool-card[data-side="${opp}"]:not(.decided)`).forEach(c =>
      c.classList.add("drop-target"));
  });
  card.addEventListener("dragend", () => {
    card.classList.remove("dragging");
    document.querySelectorAll(".drop-target,.drag-over").forEach(c =>
      c.classList.remove("drop-target","drag-over"));
    _drag = null;
  });
  card.addEventListener("dragover", e => {
    if (!_drag || card.dataset.side === _drag.side || card.classList.contains("decided")) return;
    e.preventDefault();
    document.querySelectorAll(".drag-over").forEach(c => c.classList.remove("drag-over"));
    card.classList.add("drag-over");
  });
  card.addEventListener("dragleave", () => card.classList.remove("drag-over"));
  card.addEventListener("drop", e => {
    e.preventDefault();
    if (!_drag || card.dataset.side === _drag.side || card.classList.contains("decided")) return;
    card.classList.remove("drag-over");
    const ynabId = _drag.side === "ynab" ? _drag.id : card.dataset.id;
    const bankId = _drag.side === "bank" ? _drag.id : card.dataset.id;
    openMatchConfirmModal(ynabId, bankId);
  });
}

/* ── Match confirm modal ──────────────────────────────────────────────────── */
let _pendingMatch = null;

function openMatchConfirmModal(ynabItemId, bankItemId) {
  const yi = state.ynabPool.find(i => i.id === ynabItemId);
  const bi = state.bankPool.find(i => i.id === bankItemId);
  if (!yi || !bi) return;
  _pendingMatch = { ynabItemId, bankItemId };

  document.getElementById("confirm-ynab-preview").innerHTML = `
    <div class="half-meta">
      <span class="half-date">${yi.ynab.date}</span>
      <span class="half-amount ${amountClass(yi.ynab.amount)}">${fmtEur(yi.ynab.amount)}</span>
    </div>
    <div class="half-payee">${esc(yi.ynab.payee_name)||"—"}</div>`;
  document.getElementById("confirm-bank-preview").innerHTML = `
    <div class="half-meta">
      <span class="half-date">${bi.bank.date}</span>
      <span class="half-amount ${amountClass(bi.bank.amount)}">${fmtEur(bi.bank.amount)}</span>
    </div>
    <div class="half-payee">${esc(bi.bank.payee)||"—"}</div>`;

  const diff = Math.abs(yi.ynab.amount - bi.bank.amount);
  const warnEl = document.getElementById("confirm-match-warning");
  if (diff > 0.01) {
    warnEl.textContent = `⚠️ Betragsunterschied: ${diff.toFixed(2).replace(".",",")} €`;
    warnEl.classList.remove("hidden");
  } else {
    warnEl.classList.add("hidden");
  }
  showModal("modal-match-confirm");
}

document.getElementById("confirm-match-yes").addEventListener("click", () => {
  if (!_pendingMatch) { closeModal(); return; }
  const yi = state.ynabPool.find(i => i.id === _pendingMatch.ynabItemId);
  const bi = state.bankPool.find(i => i.id === _pendingMatch.bankItemId);
  if (!yi || !bi) { closeModal(); return; }
  state.pairs.push({
    id: uid(), ynab: yi.ynab, bank: bi.bank, score: 0,
    amountDiff: Math.abs(yi.ynab.amount - bi.bank.amount),
    status: "manual", decision: null, editData: null,
  });
  state.ynabPool.splice(state.ynabPool.indexOf(yi), 1);
  state.bankPool.splice(state.bankPool.indexOf(bi), 1);
  _pendingMatch = null;
  closeModal();
  renderPairs(); renderYnabPool(); renderBankPool();
});
document.getElementById("confirm-match-no").addEventListener("click", () => {
  _pendingMatch = null; closeModal();
});

/* ── Edit pair modal ──────────────────────────────────────────────────────── */
let _editPairId = null;

function openEditPairModal(pairId) {
  const pair = state.pairs.find(p => p.id === pairId);
  if (!pair) return;
  _editPairId = pairId;
  const prevAmt = pair.editData?.amount ?? pair.bank.amount;
  document.getElementById("edit-pair-desc").textContent =
    `YNAB: ${fmtEur(pair.ynab.amount)}  →  Bank: ${fmtEur(pair.bank.amount)}`;
  document.getElementById("edit-pair-amount").value         = prevAmt.toFixed(2).replace(".",",");
  document.getElementById("edit-pair-memo").value           = pair.editData?.memo ?? pair.ynab.memo ?? "";
  document.getElementById("edit-pair-payee-input").value    = pair.editData?.payee_name ?? pair.ynab.payee_name ?? "";
  document.getElementById("edit-pair-category-input").value = "";
  document.getElementById("edit-pair-category-id").value    = pair.editData?.category_id ?? "";
  showModal("modal-edit-pair");
}

document.getElementById("edit-pair-save").addEventListener("click", () => {
  const pair = state.pairs.find(p => p.id === _editPairId);
  if (!pair) { closeModal(); return; }
  const amount = parseLocalAmount(document.getElementById("edit-pair-amount").value);
  if (amount === null) { alert("Ungültiger Betrag"); return; }
  pair.editData = {
    amount,
    memo:        document.getElementById("edit-pair-memo").value.trim() || null,
    payee_name:  document.getElementById("edit-pair-payee-input").value.trim() || null,
    category_id: document.getElementById("edit-pair-category-id").value || null,
  };
  pair.amountDiff = Math.abs(pair.ynab.amount - amount);
  pair.decision   = "update";
  _editPairId = null;
  closeModal(); renderPairs();
});
document.getElementById("edit-pair-cancel").addEventListener("click", () => {
  _editPairId = null; closeModal();
});

/* ── Apply changes ────────────────────────────────────────────────────────── */
document.getElementById("apply-btn").addEventListener("click", applyChanges);

async function applyChanges() {
  const actions = [];

  for (const p of state.pairs) {
    if (p.decision === "confirm")
      actions.push({ type: "clear", ynab_id: p.ynab.id });
    else if (p.decision === "update" && p.editData)
      actions.push({ type: "update", ynab_id: p.ynab.id,
                     amount: p.editData.amount, memo: p.editData.memo,
                     payee_name: p.editData.payee_name, category_id: p.editData.category_id });
  }
  for (const item of state.ynabPool) {
    if (item.decision === "clear")
      actions.push({ type: "clear", ynab_id: item.ynab.id });
    else if (item.decision === "defer")
      actions.push({ type: "defer", payee_name: item.ynab.payee_name || "" });
  }
  for (const item of state.bankPool) {
    if (item.decision === "create" && item.createData)
      actions.push({ type: "create", ...item.createData });
  }

  const btn = document.getElementById("apply-btn");
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Speichere…';
  try {
    const res  = await fetch(`/api/session/${state.sessionId}/apply`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actions }),
    });
    const data = await res.json();
    if (data.error) { alert(`Fehler: ${data.error}`); return; }
    await loadBalance();
    showView("reconcile");
  } catch (e) {
    alert(`Netzwerkfehler: ${e.message}`);
  } finally {
    btn.disabled = false; btn.textContent = "Speichern";
  }
}

/* ── Create transaction modal (from bank pool) ─────────────────────────────── */
let _createBankItemId = null;

function openCreateModalFromPool(itemId) {
  const item = state.bankPool.find(i => i.id === itemId);
  if (!item) return;
  _createBankItemId = itemId;
  const bt = item.bank;
  document.getElementById("create-date").value            = formatDateDE(bt.date);
  document.getElementById("create-amount").value          = bt.amount.toFixed(2).replace(".",",");
  document.getElementById("create-payee-input").value     = bt.payee;
  document.getElementById("create-category-input").value  = "";
  document.getElementById("create-category-id").value     = "";
  document.getElementById("create-memo").value            = bt.memo || "";
  showModal("modal-create");
}

document.getElementById("create-save-btn").addEventListener("click", () => {
  const dateRaw = document.getElementById("create-date").value.trim();
  const amount  = parseLocalAmount(document.getElementById("create-amount").value);
  const payee   = document.getElementById("create-payee-input").value.trim();
  const catId   = document.getElementById("create-category-id").value;
  const memo    = document.getElementById("create-memo").value.trim();
  if (!dateRaw || amount === null) { alert("Datum und Betrag sind erforderlich."); return; }
  const date = parseDateToISO(dateRaw);
  if (!date) { alert("Ungültiges Datum (TT.MM.JJJJ)"); return; }
  if (_createBankItemId) {
    const item = state.bankPool.find(i => i.id === _createBankItemId);
    if (item) {
      item.createData = { date, amount, payee_name: payee, category_id: catId||null, memo };
      item.decision   = "create";
      renderBankPool();
    }
    _createBankItemId = null;
  }
  closeModal();
});
document.getElementById("create-cancel-btn").addEventListener("click", () => {
  _createBankItemId = null; closeModal();
});

/* ── Autocomplete ─────────────────────────────────────────────────────────── */
function setupAutocomplete(inputId, listId, fetcher, onSelect) {
  const input = document.getElementById(inputId);
  const list  = document.getElementById(listId);
  let timer   = null;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const items = await fetcher(input.value.trim());
      list.innerHTML = "";
      if (!items.length) { list.classList.add("hidden"); return; }
      items.forEach(item => {
        const li = document.createElement("li");
        li.textContent = item.label;
        li.addEventListener("mousedown", e => { e.preventDefault(); onSelect(item); list.classList.add("hidden"); });
        list.appendChild(li);
      });
      list.classList.remove("hidden");
    }, 200);
  });
  input.addEventListener("blur", () => setTimeout(() => list.classList.add("hidden"), 150));
}

const payeeFetcher = async q => {
  if (!state.sessionId) return [];
  const res  = await fetch(`/api/session/${state.sessionId}/payees?q=${encodeURIComponent(q)}`);
  const data = await res.json();
  return (data.payees||[]).map(p => ({ label: p.name, id: p.id }));
};
const catFetcher = async q => {
  if (!state.sessionId) return [];
  const res  = await fetch(`/api/session/${state.sessionId}/categories?q=${encodeURIComponent(q)}`);
  const data = await res.json();
  return (data.categories||[]).map(c => ({ label: c.display, id: c.id }));
};

setupAutocomplete("create-payee-input", "create-payee-results", payeeFetcher,
  item => { document.getElementById("create-payee-input").value = item.label; });
setupAutocomplete("create-category-input", "create-category-results", catFetcher, item => {
  document.getElementById("create-category-input").value = item.label;
  document.getElementById("create-category-id").value    = item.id;
});
setupAutocomplete("edit-pair-payee-input", "edit-pair-payee-results", payeeFetcher,
  item => { document.getElementById("edit-pair-payee-input").value = item.label; });
setupAutocomplete("edit-pair-category-input", "edit-pair-category-results", catFetcher, item => {
  document.getElementById("edit-pair-category-input").value = item.label;
  document.getElementById("edit-pair-category-id").value    = item.id;
});

/* ═══════════════════════════════════════════════════════════════════════════
   RECONCILE VIEW
   ═══════════════════════════════════════════════════════════════════════════ */
async function loadBalance() {
  const res  = await fetch(`/api/session/${state.sessionId}/balance`);
  const data = await res.json();
  if (data.error) return;
  document.getElementById("balance-info").innerHTML = `
    <div class="balance-row">
      <span class="bal-label">YNAB abgeglichen</span>
      <span class="bal-value ${amountClass(data.cleared_balance)}">${fmtEur(data.cleared_balance)}</span>
    </div>
    <div class="balance-row">
      <span class="bal-label">YNAB gesamt</span>
      <span class="bal-value ${amountClass(data.balance)}">${fmtEur(data.balance)}</span>
    </div>`;
  window._clearedBalance = data.cleared_balance;
}

document.getElementById("bank-balance-input").addEventListener("input", () => {
  const amt    = parseLocalAmount(document.getElementById("bank-balance-input").value);
  const diffEl = document.getElementById("reconcile-diff");
  if (amt === null || window._clearedBalance === undefined) { diffEl.classList.add("hidden"); return; }
  const diff = Math.round((amt - window._clearedBalance) * 100) / 100;
  diffEl.classList.remove("hidden");
  if (Math.abs(diff) < 0.01) {
    diffEl.className = "reconcile-diff ok"; diffEl.textContent = "✅ Salden stimmen überein";
  } else {
    diffEl.className = "reconcile-diff off";
    diffEl.textContent = `⚠️ Differenz: ${diff >= 0 ? "+" : ""}${diff.toFixed(2).replace(".",",")} € → Ausgleichsbuchung wird erstellt`;
  }
});

document.getElementById("reconcile-btn").addEventListener("click", async () => {
  const bankBalance = parseLocalAmount(document.getElementById("bank-balance-input").value.trim());
  if (bankBalance === null) { showError("reconcile-error", "Ungültiger Betrag"); return; }
  clearError("reconcile-error");
  const btn = document.getElementById("reconcile-btn");
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>';
  try {
    const res  = await fetch(`/api/session/${state.sessionId}/reconcile`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bank_balance: bankBalance }),
    });
    const data = await res.json();
    if (data.error) { showError("reconcile-error", data.error); return; }
    let summary = `${data.reconciled} Transaktion(en) auf "reconciled" gesetzt.`;
    if (data.adjustment !== 0) summary += ` Ausgleichsbuchung: ${fmtEur(data.adjustment)}.`;
    document.getElementById("done-summary").textContent = summary;
    showView("done");
  } catch (e) {
    showError("reconcile-error", `Netzwerkfehler: ${e.message}`);
  } finally {
    btn.disabled = false; btn.textContent = "Konto reconcilen";
  }
});

document.getElementById("skip-reconcile-btn").addEventListener("click", () => {
  document.getElementById("done-summary").textContent = "Abgleich abgeschlossen. Kein Reconcile durchgeführt.";
  showView("done");
});

/* ═══════════════════════════════════════════════════════════════════════════
   DONE VIEW
   ═══════════════════════════════════════════════════════════════════════════ */
document.getElementById("restart-btn").addEventListener("click", async () => {
  Object.assign(state, {
    sessionId: null, pairs: [], ynabPool: [], bankPool: [], deferred: [],
    stats: {}, txPreview: null, txFilter: "all",
  });
  csvInput.value = ""; fileLabel.textContent = "Datei auswählen oder hierher ziehen";
  updateStartBtn();
  showView("setup");
  await loadTxPreview();
});

/* ── Boot ─────────────────────────────────────────────────────────────────── */
loadBudgets();
