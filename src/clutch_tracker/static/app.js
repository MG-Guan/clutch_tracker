const COLORS = {
  amber: "#e39a3c",
  green: "#3ecf8e",
  red: "#e25b66",
  blue: "#6aa8e8",
  purple: "#b48ee0",
  muted: "#8b98a8",
};

const state = {
  page: "overview",
  targetId: localStorage.getItem("clutch.targetId") || "",
  targets: [],
  invFilter: "active",
  reportKind: "daily",
  recTopN: 3,
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, { ...options, headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText || "请求失败");
  return data;
}

function toast(message, kind = "ok") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), 3800);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function num(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function money(value) {
  const n = num(value);
  return n === null ? "—" : `$${Math.round(n).toLocaleString("en-CA")}`;
}

function km(value) {
  const n = num(value);
  return n === null ? "—" : `${Math.round(n).toLocaleString()} km`;
}

function badge(text, cls) {
  return `<span class="badge ${cls || ""}">${escapeHtml(text)}</span>`;
}

function empty(text) {
  return `<div class="empty">
    <svg viewBox="0 0 24 24"><path d="M18.92 6.01C18.72 5.42 18.16 5 17.5 5h-11c-.66 0-1.21.42-1.42 1.01L3 12v8h3v-1h12v1h3v-8l-2.08-5.99zM6.5 16c-.83 0-1.5-.67-1.5-1.5S5.67 13 6.5 13s1.5.67 1.5 1.5S7.33 16 6.5 16zm11 0c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zM5 11l1.5-4.5h11L19 11H5z"/></svg>
    ${escapeHtml(text || "暂无数据")}
  </div>`;
}

function countBy(rows, keyFn) {
  const map = new Map();
  for (const row of rows) {
    const key = keyFn(row) || "未知";
    map.set(key, (map.get(key) || 0) + 1);
  }
  return [...map.entries()].map(([label, value]) => ({ label, value }));
}

function ring(value, max, color) {
  const r = 18;
  const c = 2 * Math.PI * r;
  const pct = max > 0 ? Math.min(value / max, 1) : 0;
  const dash = `${pct * c} ${c}`;
  return `<svg class="kpi-ring" viewBox="0 0 44 44">
    <circle cx="22" cy="22" r="${r}" fill="none" stroke="#1c232c" stroke-width="6"/>
    <circle cx="22" cy="22" r="${r}" fill="none" stroke="${color}" stroke-width="6"
      stroke-dasharray="${dash}" stroke-linecap="round" transform="rotate(-90 22 22)"/>
  </svg>`;
}

function kpi(label, value, color, max) {
  return `<div class="kpi">${ring(Number(value) || 0, max || Math.max(Number(value) || 0, 1), color)}
    <div><div class="kpi-val">${escapeHtml(value)}</div><div class="kpi-label">${escapeHtml(label)}</div></div>
  </div>`;
}

function donut(items, caption) {
  const total = items.reduce((s, i) => s + i.value, 0);
  const r = 56;
  const circ = 2 * Math.PI * r;
  let offset = 0;
  const rings = items
    .map((item) => {
      const len = total ? (item.value / total) * circ : 0;
      const el = `<circle cx="90" cy="90" r="${r}" fill="none" stroke="${item.color}" stroke-width="20"
        stroke-dasharray="${len} ${circ - len}" stroke-dashoffset="${-offset}" transform="rotate(-90 90 90)"/>`;
      offset += len;
      return el;
    })
    .join("");
  return `<div>
    <svg class="donut" viewBox="0 0 180 180">${rings}
      <circle cx="90" cy="90" r="${r}" fill="none" stroke="#1c232c" stroke-width="20" opacity="${total ? 0 : 1}"/>
      <text x="90" y="86" text-anchor="middle" class="donut-num">${total}</text>
      <text x="90" y="106" text-anchor="middle" class="donut-cap">${escapeHtml(caption || "辆")}</text>
    </svg>
    <div class="legend">${items
      .map(
        (i) =>
          `<span><span class="swatch" style="background:${i.color}"></span>${escapeHtml(i.label)} ${i.value}</span>`
      )
      .join("")}</div>
  </div>`;
}

function hbars(items, color) {
  if (!items.length) return empty();
  const max = Math.max(...items.map((i) => i.value), 1);
  return items
    .map(
      (i) => `<div class="hbar">
        <span class="hbar-label" title="${escapeHtml(i.label)}">${escapeHtml(i.label)}</span>
        <div class="hbar-track"><div class="hbar-fill" style="width:${(i.value / max) * 100}%;background:${i.color || color || COLORS.amber}"></div></div>
        <span class="hbar-val">${i.value}</span>
      </div>`
    )
    .join("");
}

function columns(items) {
  if (!items.length) return empty();
  const max = Math.max(...items.map((i) => i.value), 1);
  const w = Math.max(items.length * 36 + 24, 180);
  const h = 150;
  const bars = items
    .map((it, idx) => {
      const bh = (it.value / max) * 108;
      const x = 18 + idx * 36;
      const y = 118 - bh;
      return `<rect x="${x}" y="${y}" width="22" height="${Math.max(bh, 2)}" rx="4" fill="${it.color || COLORS.amber}"/>
        <text x="${x + 11}" y="136" text-anchor="middle" class="axis">${escapeHtml(String(it.label).slice(-2))}</text>`;
    })
    .join("");
  return `<svg class="cols" viewBox="0 0 ${w} ${h}">${bars}</svg>`;
}

function scatter(points) {
  const usable = points.filter((p) => p.x !== null && p.y !== null);
  if (!usable.length) return empty("缺价格或里程");
  const w = 440;
  const h = 220;
  const pad = { l: 44, r: 12, t: 12, b: 28 };
  const xs = usable.map((p) => p.x);
  const ys = usable.map((p) => p.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const dx = maxX - minX || 1;
  const dy = maxY - minY || 1;
  const xPos = (v) => pad.l + ((v - minX) / dx) * (w - pad.l - pad.r);
  const yPos = (v) => pad.t + (1 - (v - minY) / dy) * (h - pad.t - pad.b);
  const dots = usable
    .map(
      (p) =>
        `<circle cx="${xPos(p.x)}" cy="${yPos(p.y)}" r="5" fill="${p.color}" opacity="0.85">
          <title>${escapeHtml(p.label)}</title>
        </circle>`
    )
    .join("");
  return `<svg class="scatter" viewBox="0 0 ${w} ${h}">
    <line x1="${pad.l}" y1="${h - pad.b}" x2="${w - pad.r}" y2="${h - pad.b}" class="gridline"/>
    <line x1="${pad.l}" y1="${pad.t}" x2="${pad.l}" y2="${h - pad.b}" class="gridline"/>
    ${dots}
    <text x="${w / 2}" y="${h - 4}" text-anchor="middle" class="axis">价格 CAD</text>
    <text x="12" y="${h / 2}" class="axis" transform="rotate(-90 12 ${h / 2})">里程 km</text>
  </svg>`;
}

function sparkline(values, color) {
  if (values.length < 2) return empty("样本不足");
  const w = 420;
  const h = 90;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const d = max - min || 1;
  const pts = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * (w - 8) + 4;
      const y = 8 + (1 - (v - min) / d) * (h - 20);
      return `${x},${y}`;
    })
    .join(" ");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}">
    <polyline fill="none" stroke="${color || COLORS.amber}" stroke-width="2.5" points="${pts}"/>
  </svg>`;
}

function showPage(name) {
  state.page = name;
  document.querySelectorAll(".nav button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.page === name);
  });
  document.querySelectorAll(".page").forEach((page) => {
    page.classList.toggle("active", page.id === `page-${name}`);
  });
  render();
}

function currentTarget() {
  return state.targets.find((t) => t.target_id === state.targetId) || state.targets[0] || null;
}

function requireTargetId() {
  const target = currentTarget();
  if (!target) throw new Error("没有可用目标");
  return target.target_id;
}

function fillTargetSelect() {
  $("target-select").innerHTML = state.targets
    .map(
      (t) =>
        `<option value="${escapeHtml(t.target_id)}" ${t.target_id === state.targetId ? "selected" : ""}>${escapeHtml(
          t.label
        )}</option>`
    )
    .join("");
}

async function loadTargets() {
  const data = await api("/api/targets");
  state.targets = data.targets || [];
  if (!state.targets.some((t) => t.target_id === state.targetId)) {
    state.targetId = state.targets[0]?.target_id || "";
  }
  localStorage.setItem("clutch.targetId", state.targetId);
  fillTargetSelect();
}

async function withButton(button, fn) {
  if (!button) return fn();
  button.disabled = true;
  try {
    return await fn();
  } finally {
    button.disabled = false;
  }
}

async function loadTargetBundle() {
  const targetId = requireTargetId();
  const [inventory, observations, events] = await Promise.all([
    api(`/api/targets/${encodeURIComponent(targetId)}/inventory`),
    api(`/api/targets/${encodeURIComponent(targetId)}/observations?limit=500`),
    api(`/api/targets/${encodeURIComponent(targetId)}/events?limit=500`),
  ]);
  return { targetId, inventory, observations, events };
}

function yearBars(vehicles) {
  const years = countBy(vehicles, (v) => v.year).sort((a, b) => String(a.label).localeCompare(String(b.label)));
  return columns(years.map((y) => ({ ...y, color: COLORS.amber })));
}

async function render() {
  const page = state.page;
  try {
    if (page === "overview") await renderOverview();
    else if (page === "recs") await renderRecs();
    else if (page === "inventory") await renderInventory();
    else if (page === "history") await renderHistory();
    else if (page === "import") await renderImport();
    else if (page === "reports") await renderReports();
    else if (page === "ops") await renderOps();
  } catch (err) {
    $(`page-${page}`).innerHTML = empty(err.message);
    toast(err.message, "error");
  }
}

async function renderOverview() {
  const overview = await api("/api/overview");
  const t = overview.totals;
  let charts = "";
  try {
    const { inventory } = await loadTargetBundle();
    const vehicles = inventory.vehicles || [];
    const active = vehicles.filter((v) => v.status === "active");
    charts = `
      <div class="charts">
        <div class="panel">
          <div class="panel-title">在售 / 下架</div>
          ${donut(
            [
              { label: "在售", value: active.length, color: COLORS.green },
              { label: "下架", value: vehicles.length - active.length, color: COLORS.red },
            ],
            "库存"
          )}
        </div>
        <div class="panel">
          <div class="panel-title">年款分布</div>
          ${yearBars(active.length ? active : vehicles)}
        </div>
        <div class="panel">
          <div class="panel-title">价格 × 里程</div>
          ${scatter(
            vehicles.map((v) => ({
              x: num(v.price_cad),
              y: num(v.mileage_km),
              label: `${v.year || ""} ${v.vin} ${money(v.price_cad)}`,
              color: v.status === "active" ? COLORS.amber : COLORS.muted,
            }))
          )}
        </div>
        <div class="panel">
          <div class="panel-title">事故</div>
          ${hbars(
            countBy(active, (v) => v.accident_history_status).map((i) => ({
              ...i,
              color: i.label === "reported" ? COLORS.red : i.label === "none" ? COLORS.green : COLORS.muted,
            }))
          )}
        </div>
      </div>`;
  } catch {
    charts = empty("还没有库存，先导入扫描");
  }

  $("page-overview").innerHTML = `
    <div class="kpis">
      ${kpi("在售", t.active_listings, COLORS.green, Math.max(t.active_listings + t.removed_listings, 1))}
      ${kpi("下架", t.removed_listings, COLORS.red, Math.max(t.active_listings + t.removed_listings, 1))}
      ${kpi("VIN", t.tracked_vins, COLORS.blue, Math.max(t.tracked_vins, 1))}
      ${kpi("目标", t.targets, COLORS.amber, Math.max(t.targets, 1))}
    </div>
    <div class="ops-grid" style="margin-bottom:12px">
      <button class="op-tile" id="btn-goto-recs" type="button">
        <svg viewBox="0 0 24 24"><path d="M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z"/></svg>
        当前推荐
        <span class="muted">只看现在还能买的车</span>
      </button>
      <button class="op-tile" id="btn-goto-import" type="button">
        <svg viewBox="0 0 24 24"><path d="M9 16h6v-6h4l-7-7-7 7h4v6zm-4 2h14v2H5v-2z"/></svg>
        扫描
        <span class="muted">打开 Clutch，导入最新 JSON</span>
      </button>
      <button class="op-tile" id="btn-goto-inv" type="button">
        <svg viewBox="0 0 24 24"><path d="M18.92 6.01C18.72 5.42 18.16 5 17.5 5h-11c-.66 0-1.21.42-1.42 1.01L3 12v8h3v-1h12v1h3v-8l-2.08-5.99zM6.5 16c-.83 0-1.5-.67-1.5-1.5S5.67 13 6.5 13s1.5.67 1.5 1.5S7.33 16 6.5 16zm11 0c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zM5 11l1.5-4.5h11L19 11H5z"/></svg>
        查看在售
        <span class="muted">库存卡片和打开链接</span>
      </button>
    </div>
    ${charts}
  `;
  $("btn-goto-recs").addEventListener("click", () => showPage("recs"));
  $("btn-goto-inv").addEventListener("click", () => showPage("inventory"));
  $("btn-goto-import").addEventListener("click", () => showPage("import"));
}

function recPickCard(pick) {
  const url = pick.listing_url;
  const title = [pick.year, pick.trim].filter(Boolean).join(" ") || pick.vin;
  const priceLabel = money(pick.price_cad) === "—" ? pick.price_cad || "—" : money(pick.price_cad);
  const kmLabel = km(pick.mileage_km) === "—" ? pick.mileage_km || "—" : km(pick.mileage_km);
  return `<article class="vcard">
    <div class="vcard-top">
      <div>
        <div class="vcard-title">${escapeHtml(title)}</div>
        <div class="vcard-sub rec-metric">${escapeHtml(pick.metric || "")}</div>
      </div>
      ${badge("在售", "on")}
    </div>
    <div class="chips">
      <span class="chip"><b>价</b>${escapeHtml(priceLabel)}</span>
      <span class="chip"><b>里程</b>${escapeHtml(kmLabel)}</span>
      ${pick.accident_history ? `<span class="chip">${escapeHtml(pick.accident_history)}</span>` : ""}
      ${pick.usage_history && pick.usage_history !== "unknown" ? `<span class="chip">${escapeHtml(pick.usage_history)}</span>` : ""}
    </div>
    <div class="vcard-top">
      <span class="vin">${escapeHtml(pick.vin)}</span>
      ${
        url
          ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener">打开</a>`
          : `<span class="muted">无链接</span>`
      }
    </div>
  </article>`;
}

async function renderRecs() {
  const targetId = requireTargetId();
  const topN = state.recTopN || 3;
  const data = await api(
    `/api/targets/${encodeURIComponent(targetId)}/recommendations?top_n=${encodeURIComponent(topN)}`
  );
  const recs = data.recommendations || {};
  const currentPicks = recs.current_picks || [];
  const sections = (recs.sections || []).filter((section) => (section.picks || []).length);
  const scanLabel = recs.scan_id ? `扫描 ${recs.scan_id}` : "还没有扫描";
  const warning =
    recs.scan_id && !recs.scan_complete
      ? `<div class="banner warn">最近一次扫描是部分扫描，推荐可能漏车。点左侧「扫描」导入一次完整结果。</div>`
      : "";
  const currentBlock =
    currentPicks.length > 0
      ? `<section class="rec-section">
            <h2>当前有效推荐</h2>
            <p>只包含现在在售、可以推荐的车。点「打开」应能买。偏好配置（${escapeHtml(
              recs.preference_summary || "无"
            )}）会另外标出。</p>
            <div class="cards">${currentPicks.map(recPickCard).join("")}</div>
          </section>`
      : `<div class="empty-action">
            ${empty("现在没有有效推荐。先扫描一次当前库存。")}
            <button class="btn primary" id="recs-goto-scan" type="button">去扫描</button>
          </div>`;

  $("page-recs").innerHTML = `
    <div class="banner">
      这里只展示<strong>当前在售</strong>车辆。下架、外省可买、已售的不会出现。
    </div>
    ${warning}
    <div class="kpis">
      ${kpi("在售", recs.active_listings || 0, COLORS.green, Math.max(recs.active_listings || 0, 1))}
      ${kpi("可推荐", recs.recommendable_listings || 0, COLORS.amber, Math.max(recs.active_listings || 0, 1))}
      ${kpi("偏好匹配", recs.preference_matched || 0, COLORS.blue, Math.max(recs.active_listings || 0, 1))}
      ${kpi("排除", recs.excluded || 0, COLORS.red, Math.max(recs.active_listings || 0, 1))}
    </div>
    <div class="row rec-toolbar">
      ${
        recs.scan_id
          ? recs.scan_complete
            ? badge("完整扫描", "on")
            : badge("部分扫描", "warn")
          : badge("无扫描", "")
      }
      <span class="muted mono">${escapeHtml(scanLabel)}</span>
      <span class="muted">${escapeHtml(recs.inventory_updated_at || recs.generated_at || "")}</span>
      <span style="flex:1"></span>
      <button class="btn" id="rec-scan" type="button">扫描</button>
      <label class="muted">每维 top
        <input id="rec-top-n" type="number" min="1" value="${escapeHtml(topN)}" />
      </label>
      <button class="btn" id="rec-refresh" type="button">刷新</button>
      <button class="btn primary" id="rec-write" type="button">写入报告</button>
    </div>
    ${currentBlock}
    ${sections
      .map((section) => {
        const picks = section.picks || [];
        return `<section class="rec-section">
            <h2>${escapeHtml(section.title || "")}</h2>
            <p>${escapeHtml(section.description || "")}</p>
            <div class="cards">${picks.map(recPickCard).join("")}</div>
          </section>`;
      })
      .join("")}
  `;

  const goScan = () => showPage("import");
  $("rec-scan")?.addEventListener("click", goScan);
  $("recs-goto-scan")?.addEventListener("click", goScan);
  $("rec-top-n")?.addEventListener("change", () => {
    const next = Number($("rec-top-n").value || 3);
    state.recTopN = Number.isFinite(next) && next >= 1 ? next : 3;
  });
  $("rec-refresh")?.addEventListener("click", async (ev) => {
    state.recTopN = Number($("rec-top-n").value || 3) || 3;
    await withButton(ev.currentTarget, () => renderRecs());
  });
  $("rec-write")?.addEventListener("click", async (ev) => {
    state.recTopN = Number($("rec-top-n").value || 3) || 3;
    await withButton(ev.currentTarget, async () => {
      const result = await api("/api/actions/generate-recommendations", {
        method: "POST",
        body: JSON.stringify({ target_id: targetId, top_n: state.recTopN }),
      });
      toast(`已写入 ${result.path}`);
      await renderRecs();
    });
  });
}

async function renderInventory() {
  const { inventory } = await loadTargetBundle();
  const vehicles = inventory.vehicles || [];
  const active = vehicles.filter((v) => v.status === "active");
  const prices = active.map((v) => num(v.price_cad)).filter((n) => n !== null);
  const miles = active.map((v) => num(v.mileage_km)).filter((n) => n !== null);
  const maxPrice = Math.max(...prices, 1);
  const maxKm = Math.max(...miles, 1);

  $("page-inventory").innerHTML = `
    <div class="row">
      <div class="seg" id="inv-seg">
        <button type="button" data-f="active" class="${state.invFilter === "active" ? "active" : ""}">在售 ${active.length}</button>
        <button type="button" data-f="all" class="${state.invFilter === "all" ? "active" : ""}">全部 ${vehicles.length}</button>
        <button type="button" data-f="removed" class="${state.invFilter === "removed" ? "active" : ""}">下架 ${
          vehicles.length - active.length
        }</button>
      </div>
      <input id="inv-q" type="search" placeholder="VIN / 年款 / 配置" style="min-width:200px" />
      ${inventory.scan_complete ? badge("完整扫描", "on") : badge("部分扫描", "warn")}
    </div>
    <div class="charts" style="margin-bottom:12px">
      <div class="panel"><div class="panel-title">价格 × 里程</div>${scatter(
        (state.invFilter === "removed" ? vehicles.filter((v) => v.status !== "active") : state.invFilter === "all" ? vehicles : active).map(
          (v) => ({
            x: num(v.price_cad),
            y: num(v.mileage_km),
            label: `${v.year || ""} ${v.vin}`,
            color: v.status === "active" ? COLORS.amber : COLORS.red,
          })
        )
      )}</div>
      <div class="panel"><div class="panel-title">年款</div>${yearBars(
        state.invFilter === "removed"
          ? vehicles.filter((v) => v.status !== "active")
          : state.invFilter === "all"
            ? vehicles
            : active
      )}</div>
    </div>
    <div id="inv-cards" class="cards"></div>
  `;

  const draw = () => {
    const q = ($("inv-q").value || "").trim().toLowerCase();
    let rows = vehicles;
    if (state.invFilter === "active") rows = rows.filter((v) => v.status === "active");
    if (state.invFilter === "removed") rows = rows.filter((v) => v.status !== "active");
    if (q) {
      rows = rows.filter((v) => [v.vin, v.year, v.make, v.model, v.trim, v.status].join(" ").toLowerCase().includes(q));
    }
    $("inv-cards").innerHTML =
      rows
        .map((v) => {
          const p = num(v.price_cad);
          const m = num(v.mileage_km);
          const accident = v.accident_history_status || "unknown";
          const use = v.previous_use || "unknown";
          return `<article class="vcard">
            <div class="vcard-top">
              <div>
                <div class="vcard-title">${escapeHtml([v.year, v.make, v.model].filter(Boolean).join(" ") || v.vin)}</div>
                <div class="vcard-sub">${escapeHtml(v.trim || "")}</div>
              </div>
              ${v.status === "active" ? badge("在售", "on") : badge("下架", "removed")}
            </div>
            <div class="meter"><span>价格</span><div class="meter-track"><div class="meter-fill" style="width:${
              p ? (p / maxPrice) * 100 : 0
            }%"></div></div><b>${money(v.price_cad)}</b></div>
            <div class="meter"><span>里程</span><div class="meter-track"><div class="meter-fill" style="width:${
              m ? (m / maxKm) * 100 : 0
            }%;background:${COLORS.blue}"></div></div><b>${km(v.mileage_km)}</b></div>
            <div class="chips">
              ${badge(accident === "reported" ? "事故" : accident === "none" ? "无事故" : "事故未知", accident === "reported" ? "removed" : accident === "none" ? "on" : "")}
              ${badge(use === "commercial" ? "商用" : use === "unknown" ? "用途未知" : use, use === "commercial" ? "warn" : "")}
            </div>
            <div class="vcard-top">
              <span class="vin">${escapeHtml(v.vin)}</span>
              ${v.listing_url ? `<a href="${escapeHtml(v.listing_url)}" target="_blank" rel="noopener">打开</a>` : ""}
            </div>
          </article>`;
        })
        .join("") || empty("没有匹配车辆");
  };

  $("inv-seg").querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.invFilter = btn.dataset.f;
      renderInventory();
    });
  });
  $("inv-q").addEventListener("input", draw);
  draw();
}

async function renderHistory() {
  const { observations, events } = await loadTargetBundle();
  const obs = observations.observations || [];
  const evs = [...(events.events || [])].reverse();
  const prices = obs.map((o) => num(o.price_cad)).filter((n) => n !== null);
  const eventTypes = countBy(events.events || [], (e) => e.event_type);
  const palette = {
    appeared: COLORS.green,
    removed: COLORS.red,
    price_changed: COLORS.blue,
  };

  $("page-history").innerHTML = `
    <div class="charts">
      <div class="panel">
        <div class="panel-title">价格走势</div>
        ${sparkline(prices, COLORS.amber)}
      </div>
      <div class="panel">
        <div class="panel-title">事件类型</div>
        ${donut(
          eventTypes.map((i) => ({ ...i, color: palette[i.label] || COLORS.amber })),
          "次"
        )}
      </div>
    </div>
    <h2>最近事件</h2>
    <div class="feed">
      ${
        evs
          .slice(0, 24)
          .map(
            (e) => `<div class="feed-item">
              <span class="feed-dot ${escapeHtml(e.event_type || "")}"></span>
              <div><b>${escapeHtml(e.event_type || "")}</b> <span class="vin">${escapeHtml(e.vin)}</span></div>
              <span class="muted mono">${escapeHtml((e.event_at || "").slice(0, 16))}</span>
            </div>`
          )
          .join("") || empty()
      }
    </div>
  `;
}

async function renderImport() {
  const targetId = requireTargetId();
  const [scans, detail] = await Promise.all([
    api("/api/scans"),
    api(`/api/targets/${encodeURIComponent(targetId)}`),
  ]);
  const searchUrl = detail.target?.search_url;
  const c = detail.target?.criteria || {};
  $("page-import").innerHTML = `
    <div class="banner">
      <strong>扫描</strong>：网页不会自己去 Clutch 抓取。打开搜索页用浏览器扫完，把 JSON 拖到下面，再回「推荐」看当前还能买的车。
    </div>
    <div class="ops-grid" style="margin-bottom:12px">
      ${
        searchUrl
          ? `<a class="op-tile" id="open-clutch" href="${escapeHtml(searchUrl)}" target="_blank" rel="noopener">
              <svg viewBox="0 0 24 24"><path d="M19 19H5V5h7V3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2v-7h-2v7zM14 3v2h3.59l-9.83 9.83 1.41 1.41L19 6.41V10h2V3h-7z"/></svg>
              打开 Clutch 搜索
              <span class="muted">${escapeHtml([c.make, c.model, c.province].filter(Boolean).join(" · "))}</span>
            </a>`
          : ""
      }
      <button class="op-tile" id="scan-goto-recs" type="button">
        <svg viewBox="0 0 24 24"><path d="M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z"/></svg>
        看当前推荐
        <span class="muted">导入之后点这里</span>
      </button>
    </div>
    <div id="dropzone" class="dropzone">
      <svg class="drop-icon" viewBox="0 0 24 24"><path d="M9 16h6v-6h4l-7-7-7 7h4v6zm-4 2h14v2H5v-2z"/></svg>
      <div>把扫描 JSON 拖到这里，或点这里选文件</div>
      <input id="scan-file" type="file" accept="application/json,.json" hidden />
    </div>
    <textarea id="scan-json" placeholder="或粘贴扫描 JSON"></textarea>
    <div class="row"><button class="btn primary" id="import-btn" type="button">导入扫描</button></div>
    <div id="import-result"></div>
    <h2>已归档 ${scans.scans?.length || 0}</h2>
    <div class="chips">${
      (scans.scans || [])
        .map((s) => `<span class="chip">${escapeHtml(s.name.replace(/\.json$/, ""))}</span>`)
        .join("") || empty("还没有扫描")
    }</div>
  `;

  $("scan-goto-recs").addEventListener("click", () => showPage("recs"));
  const dropzone = $("dropzone");
  const fileInput = $("scan-file");
  dropzone.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("dragover", (ev) => {
    ev.preventDefault();
    dropzone.classList.add("drag");
  });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag"));
  dropzone.addEventListener("drop", async (ev) => {
    ev.preventDefault();
    dropzone.classList.remove("drag");
    const file = ev.dataTransfer.files[0];
    if (file) $("scan-json").value = await file.text();
  });
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files[0];
    if (file) $("scan-json").value = await file.text();
  });
  $("import-btn").addEventListener("click", async (ev) => {
    await withButton(ev.currentTarget, async () => {
      let payload;
      try {
        payload = JSON.parse($("scan-json").value);
      } catch {
        toast("JSON 无法解析", "error");
        return;
      }
      const result = await api("/api/scans", { method: "POST", body: JSON.stringify(payload) });
      const s = result.summary;
      toast(`已导入 ${s.scan_id}`);
      $("import-result").innerHTML = `<div class="kpis" style="margin-top:12px">
        ${kpi("扫描车辆", s.vehicles_scanned, COLORS.amber)}
        ${kpi("在售", s.active_inventory, COLORS.green)}
        ${kpi("下架", s.removed_inventory, COLORS.red)}
        ${kpi("事件", s.events_added, COLORS.blue)}
      </div>
      <div class="row" style="margin-top:12px">
        <button class="btn primary" id="import-goto-recs" type="button">查看当前推荐</button>
      </div>`;
      $("import-goto-recs").addEventListener("click", () => showPage("recs"));
      await loadTargets();
    });
  });
}

function renderMarkdown(md) {
  const lines = String(md || "").replaceAll("\r\n", "\n").split("\n");
  const html = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("|") && i + 1 < lines.length && /^\|?\s*-+/.test(lines[i + 1])) {
      const rows = [];
      while (i < lines.length && lines[i].startsWith("|")) {
        rows.push(lines[i]);
        i += 1;
      }
      const parsed = rows
        .filter((_, idx) => idx !== 1)
        .map((row) =>
          row
            .replace(/^\|/, "")
            .replace(/\|$/, "")
            .split("|")
            .map((cell) => inlineMd(cell.trim()))
        );
      html.push(
        `<div class="table-wrap"><table><thead><tr>${parsed[0].map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${parsed
          .slice(1)
          .map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`)
          .join("")}</tbody></table></div>`
      );
      continue;
    }
    if (line.startsWith("### ")) html.push(`<h3>${inlineMd(line.slice(4))}</h3>`);
    else if (line.startsWith("## ")) html.push(`<h2>${inlineMd(line.slice(3))}</h2>`);
    else if (line.startsWith("# ")) html.push(`<h1>${inlineMd(line.slice(2))}</h1>`);
    else if (line.startsWith("- ")) html.push(`<div>• ${inlineMd(line.slice(2))}</div>`);
    else if (line.trim() === "") html.push("<br>");
    else html.push(`<p>${inlineMd(line)}</p>`);
    i += 1;
  }
  return `<div class="report">${html.join("")}</div>`;
}

function inlineMd(text) {
  let out = escapeHtml(text);
  out = out.replace(/`([^`]+)`/g, '<code class="mono">$1</code>');
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  return out;
}

async function renderReports() {
  const targetId = requireTargetId();
  const kind = state.reportKind;
  const pageEl = $("page-reports");
  const list = await api(`/api/targets/${encodeURIComponent(targetId)}/reports/${kind}`);
  pageEl.innerHTML = `
    <div class="row">
      <div class="seg" id="rep-seg">
        <button type="button" data-k="daily" class="${kind === "daily" ? "active" : ""}">日报</button>
        <button type="button" data-k="recommendations" class="${kind === "recommendations" ? "active" : ""}">推荐</button>
      </div>
      ${kind === "recommendations" ? `<input class="top-n" type="number" min="1" value="3" style="width:64px">` : ""}
      <button class="btn primary gen-btn" type="button">生成</button>
    </div>
    <div class="split">
      <div class="file-list report-list"></div>
      <div class="report-view">${empty("选择报告")}</div>
    </div>
  `;
  const listEl = pageEl.querySelector(".report-list");
  const viewEl = pageEl.querySelector(".report-view");
  listEl.innerHTML =
    (list.reports || [])
      .map((r) => `<button class="btn" type="button" data-name="${escapeHtml(r.name)}">${escapeHtml(r.name.replace(".md", ""))}</button>`)
      .join("") || empty("还没有报告");

  const openReport = async (name) => {
    const data = await api(`/api/targets/${encodeURIComponent(targetId)}/reports/${kind}/${encodeURIComponent(name)}`);
    viewEl.innerHTML = renderMarkdown(data.content);
  };
  listEl.querySelectorAll("button[data-name]").forEach((btn) => {
    btn.addEventListener("click", () => openReport(btn.dataset.name));
  });
  if (list.reports?.[0]) openReport(list.reports[0].name).catch((err) => toast(err.message, "error"));

  $("rep-seg").querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.reportKind = btn.dataset.k;
      renderReports();
    });
  });
  pageEl.querySelector(".gen-btn").addEventListener("click", async (ev) => {
    await withButton(ev.currentTarget, async () => {
      const path = kind === "recommendations" ? "/api/actions/generate-recommendations" : "/api/actions/generate-report";
      const body = { target_id: targetId };
      if (kind === "recommendations") body.top_n = Number(pageEl.querySelector(".top-n").value || 3);
      const result = await api(path, { method: "POST", body: JSON.stringify(body) });
      toast(`已写入 ${result.path}`);
      await renderReports();
    });
  });
}

async function renderOps() {
  const targetId = requireTargetId();
  const detail = await api(`/api/targets/${encodeURIComponent(targetId)}`);
  const c = detail.target.criteria || {};
  $("page-ops").innerHTML = `
    <div class="panel" style="margin-bottom:12px">
      <div class="panel-title">${escapeHtml(detail.target.label || targetId)}</div>
      <div class="chips">
        ${c.make ? `<span class="chip"><b>品牌</b>${escapeHtml(c.make)}</span>` : ""}
        ${c.model ? `<span class="chip"><b>车型</b>${escapeHtml(c.model)}</span>` : ""}
        ${c.province ? `<span class="chip"><b>省</b>${escapeHtml(c.province)}</span>` : ""}
        ${c.min_year || c.max_year ? `<span class="chip"><b>年款</b>${escapeHtml(c.min_year || "?")}–${escapeHtml(c.max_year || "?")}</span>` : ""}
        ${(c.model_aliases || []).map((a) => `<span class="chip">${escapeHtml(a)}</span>`).join("")}
      </div>
    </div>
    <div class="ops-grid">
      <button class="op-tile" id="btn-config" type="button">
        <svg viewBox="0 0 24 24"><path d="M9 16.2l-3.5-3.5L4 14.2 9 19l12-12-1.5-1.4z"/></svg>
        校验配置
      </button>
      <button class="op-tile" id="btn-init" type="button">
        <svg viewBox="0 0 24 24"><path d="M10 4H4v16h16V8h-8l-2-4z"/></svg>
        初始化
      </button>
      <button class="op-tile" id="btn-repo" type="button">
        <svg viewBox="0 0 24 24"><path d="M4 6h16v2H4V6zm0 5h16v2H4v-2zm0 5h10v2H4v-2z"/></svg>
        校验仓库
      </button>
      <button class="op-tile" id="btn-doctor" type="button">
        <svg viewBox="0 0 24 24"><path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-2 10h-4v4h-2v-4H7v-2h4V7h2v4h4v2z"/></svg>
        环境
      </button>
      <button class="op-tile" id="btn-restart-ops" type="button">
        <svg viewBox="0 0 24 24"><path d="M12 6V3L8 7l4 4V8c2.8 0 5 2.2 5 5a5 5 0 0 1-8.9 3.1L6.7 17.5A7 7 0 0 0 19 13c0-3.9-3.1-7-7-7z"/></svg>
        重启服务
      </button>
    </div>
    <div id="ops-out"></div>
  `;

  const showStatus = (title, data) => {
    const errors = data.errors || data.warnings || [];
    const ok = data.ok && !(data.errors && data.errors.length);
    const rows = [
      ...(data.errors || []).map((m) => ({ cls: "bad", m })),
      ...(data.warnings || []).map((m) => ({ cls: "warn", m })),
    ];
    if (data.healthy === true) rows.unshift({ cls: "ok", m: "环境正常" });
    if (data.initialized) rows.push({ cls: "ok", m: `已初始化 ${data.initialized.length} 个目标` });
    if (!rows.length && ok) rows.push({ cls: "ok", m: "通过" });
    $("ops-out").innerHTML = `<h2>${escapeHtml(title)}</h2><div class="status-list">${rows
      .map((r) => `<div class="status-row"><span class="dot ${r.cls}"></span><span>${escapeHtml(r.m)}</span></div>`)
      .join("")}</div>`;
  };

  $("btn-config").addEventListener("click", async (ev) => {
    await withButton(ev.currentTarget, async () => {
      const data = await api("/api/actions/validate-config", { method: "POST", body: "{}" });
      toast(data.ok ? "配置有效" : "配置有错误", data.ok ? "ok" : "error");
      showStatus("配置", data);
    });
  });
  $("btn-init").addEventListener("click", async (ev) => {
    await withButton(ev.currentTarget, async () => {
      const data = await api("/api/actions/initialize-targets", { method: "POST", body: "{}" });
      toast(`已初始化 ${data.initialized.length} 个目标`);
      showStatus("初始化", data);
      await loadTargets();
    });
  });
  $("btn-repo").addEventListener("click", async (ev) => {
    await withButton(ev.currentTarget, async () => {
      const data = await api("/api/actions/validate-repository", { method: "POST", body: "{}" });
      toast(data.ok ? "仓库有效" : "仓库有错误", data.ok ? "ok" : "error");
      showStatus("仓库", data);
    });
  });
  $("btn-doctor").addEventListener("click", async (ev) => {
    await withButton(ev.currentTarget, async () => {
      const data = await api("/api/doctor");
      toast(data.healthy ? "环境正常" : "环境有警告", data.healthy ? "ok" : "error");
      showStatus("环境", data);
    });
  });
  $("btn-restart-ops").addEventListener("click", () => restartServe());
}

document.querySelectorAll(".nav button").forEach((btn) => {
  btn.addEventListener("click", () => showPage(btn.dataset.page));
});

$("target-select").addEventListener("change", () => {
  state.targetId = $("target-select").value;
  localStorage.setItem("clutch.targetId", state.targetId);
  render();
});

async function waitForServer(tries = 50) {
  for (let i = 0; i < tries; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 400));
    try {
      const res = await fetch("/api/overview", { cache: "no-store" });
      if (res.ok) return;
    } catch {
      /* process is still coming up */
    }
  }
  throw new Error("重启后服务未恢复，请在终端查看 serve 输出");
}

async function restartServe() {
  const mask = $("restart-mask");
  const button = $("btn-restart");
  await withButton(button, async () => {
    mask.hidden = false;
    try {
      const data = await api("/api/actions/restart", { method: "POST", body: "{}" });
      if (!data.scheduled) {
        mask.hidden = true;
        toast(data.error || "当前无法重启", "error");
        return;
      }
      toast("正在重启 serve…");
      await waitForServer();
      location.reload();
    } catch (err) {
      try {
        await waitForServer();
        location.reload();
        return;
      } catch {
        mask.hidden = true;
        toast(err.message, "error");
      }
    }
  });
}

$("btn-restart").addEventListener("click", () => restartServe());

loadTargets()
  .then(() => render())
  .catch((err) => toast(err.message, "error"));
