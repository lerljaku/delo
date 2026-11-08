// "Site visitors" section of the admin page: GET /api/admin/stats (backend/delo/stats.py).
// One series (daily unique visitors) as bars; page views go in the tiles and the table, not a
// second axis.
import { api, esc, showError } from "./common.js";

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const fmtDay = (iso, opts) => new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, opts);
let chart = null;

function tile(label, s) {
  return `<div class="tile"><div class="label">${label}</div><div class="value">${s.visitors}</div>
    <div class="sub">visitors · ${s.views} page views</div></div>`;
}

function topList(title, rows, empty) {
  return `<div><h3>${title}</h3>${rows.length ? `<div class="table-wrap"><table><tbody>
    ${rows.map((r) => `<tr><td>${esc(r.name)}</td><td class="num">${r.views}</td></tr>`).join("")}</tbody></table></div>`
    : `<p class="muted">${empty}</p>`}</div>`;
}

function renderChart(canvas, days) {
  chart?.destroy();
  const series = css("--series-1");
  chart = new Chart(canvas, {
    type: "bar",
    data: {
      labels: days.map((d) => d.date),
      datasets: [{ data: days.map((d) => d.visitors), backgroundColor: series, hoverBackgroundColor: series,
        borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: "bottom", maxBarThickness: 18, categoryPercentage: 0.8 }],
    },
    options: {
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: css("--surface-1"), titleColor: css("--text-primary"), bodyColor: css("--text-secondary"),
          borderColor: css("--axis"), borderWidth: 1, displayColors: false, padding: 10,
          callbacks: {
            title: (items) => fmtDay(days[items[0].dataIndex].date, { weekday: "short", day: "numeric", month: "short" }),
            label: (item) => {
              const d = days[item.dataIndex];
              return [`${d.visitors} visitor${d.visitors === 1 ? "" : "s"}`, `${d.views} page view${d.views === 1 ? "" : "s"}`];
            },
          },
        },
      },
      scales: {
        x: { ticks: { color: css("--text-muted"), maxRotation: 0, autoSkip: true, maxTicksLimit: 8,
          callback: (_, i) => fmtDay(days[i].date, { day: "numeric", month: "short" }) },
          grid: { display: false }, border: { color: css("--axis") } },
        y: { beginAtZero: true, ticks: { color: css("--text-muted"), precision: 0 }, grid: { color: css("--grid") }, border: { display: false } },
      },
    },
  });
}

async function load(box, days) {
  const s = await api("/admin/stats", { query: { days } });
  const t = s.totals;
  box.querySelector("#visitor-data").innerHTML = `
    <div class="tiles visitor-tiles">
      ${tile("Today", t.today)}
      ${tile("Last 7 days", t.last7Days)}
      ${tile(`Last ${s.days.length} days`, t.period)}
    </div>
    <h3>Visitors per day</h3>
    <div class="card chart-card"><canvas id="visitors-chart" role="img"
      aria-label="Unique visitors per day over the last ${s.days.length} days"></canvas></div>
    <details style="margin-top:8px"><summary class="secondary">Show as table</summary>
      <div class="table-wrap"><table><thead><tr><th>Date</th><th class="num">Visitors</th><th class="num">Page views</th></tr></thead>
      <tbody>${s.days.slice().reverse().map((d) => `<tr><td>${esc(d.date)}</td><td class="num">${d.visitors}</td>
        <td class="num">${d.views}</td></tr>`).join("")}</tbody></table></div></details>
    <div class="visitor-lists">
      ${topList("Top pages", s.pages, "No page views yet.")}
      ${topList("Coming from", s.referrers, "No visits from other sites yet.")}
    </div>`;
  renderChart(box.querySelector("#visitors-chart"), s.days);
}

export function renderVisitors(box) {
  box.innerHTML = `<div class="toolbar"><label class="row">Period <select id="visitor-days">
      <option value="7">Last 7 days</option><option value="30" selected>Last 30 days</option><option value="90">Last 90 days</option></select></label></div>
    <p class="muted small">Counted without cookies: bots, admins and browsers with Do Not Track are left out. A visitor is recognised
      within one day only, so the multi-day totals add up daily visitors.</p>
    <div id="visitor-data"><p class="muted">Loading…</p></div>`;
  const select = box.querySelector("#visitor-days");
  const reload = () => load(box, select.value).catch((err) => showError(box.querySelector("#visitor-data"), err));
  select.addEventListener("change", reload);
  reload();
}
