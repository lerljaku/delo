import {
  api, badge, esc, fmtDelta, fmtPct, fmtRating, formatBadge, formatSelect, getFormat, getLadder, ladderLabel, ladderToggle, param, playerLink,
  renderNav, resultCell, showError, typeBadge,
} from "./common.js";

renderNav("");
const content = document.getElementById("content");
const pid = param("id");
let ladder = getLadder();
let format = getFormat();
let chart = null;

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function tile(label, value, sub = "", cls = "") {
  return `<div class="tile"><div class="label">${label}</div><div class="value ${cls}">${value}</div>${sub ? `<div class="sub">${sub}</div>` : ""}</div>`;
}

function matchupTile(label, mu) {
  if (!mu) return tile(label, '<span class="muted">—</span>', "not enough data");
  return tile(label, playerLink(mu.playerId, mu.displayName, ladder, format), `${mu.wins}-${mu.losses}-${mu.draws} in ${mu.matches} match${mu.matches === 1 ? "" : "es"}`, "name");
}

function renderChart(history, initial) {
  const canvas = document.getElementById("elo-chart");
  const points = [{ x: 0, y: initial, h: null }, ...history.map((h) => ({ x: h.seq, y: h.ratingAfter, h }))];
  chart?.destroy();
  chart = new Chart(canvas, {
    type: "line",
    data: { datasets: [{ data: points, borderColor: css("--series-1"), borderWidth: 2, pointRadius: 0, pointHoverRadius: 5,
      pointHoverBackgroundColor: css("--series-1"), pointHoverBorderColor: css("--surface-1"), pointHoverBorderWidth: 2, tension: 0 }] },
    options: {
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: css("--surface-1"), titleColor: css("--text-primary"), bodyColor: css("--text-secondary"),
          borderColor: css("--axis"), borderWidth: 1, displayColors: false, padding: 10,
          callbacks: {
            title: (items) => {
              const h = items[0].raw.h;
              return h ? `Elo ${fmtRating(h.ratingAfter)} (${h.delta >= 0 ? "+" : ""}${h.delta.toFixed(1)})` : `Starting rating ${fmtRating(initial)}`;
            },
            label: (item) => {
              const h = item.raw.h;
              return h ? [`${h.date} · round ${h.round}`, `${h.result} ${h.score} vs ${h.opponentName}`] : "";
            },
          },
        },
      },
      scales: {
        x: { type: "linear", min: 0, max: history.length, title: { display: true, text: "Rated matches", color: css("--text-muted") },
          ticks: { color: css("--text-muted"), precision: 0 }, grid: { display: false }, border: { color: css("--axis") } },
        y: { title: { display: true, text: "Elo", color: css("--text-muted") }, ticks: { color: css("--text-muted") },
          grid: { color: css("--grid") }, border: { display: false } },
      },
    },
    plugins: [{
      id: "crosshair",
      afterDatasetsDraw(c) {
        const active = c.tooltip?.getActiveElements?.();
        if (!active?.length) return;
        const { ctx, chartArea } = c;
        ctx.save();
        ctx.strokeStyle = css("--axis");
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(active[0].element.x, chartArea.top);
        ctx.lineTo(active[0].element.x, chartArea.bottom);
        ctx.stroke();
        ctx.restore();
      },
    }],
  });
}

function historyTable(history) {
  return `<div class="table-wrap"><table><thead><tr><th class="num">#</th><th>Date</th><th>Opponent</th><th>Result</th>
    <th class="num">Score</th><th class="num">Elo</th><th class="num">Change</th></tr></thead><tbody>
    ${history.slice().reverse().map((h) => `<tr><td class="num">${h.seq}</td><td>${esc(h.date)}</td>
      <td>${playerLink(h.opponentId, h.opponentName, ladder, format)}</td><td>${resultCell(h.result)}</td>
      <td class="num">${esc(h.score)}</td><td class="num">${fmtRating(h.ratingAfter)}</td><td class="num">${fmtDelta(h.delta)}</td></tr>`).join("")}
    </tbody></table></div>`;
}

function tournamentHistory(tournaments, showElo = true) {
  return tournaments.map((t) => `<details class="tournament"><summary>
      <span class="name"><a href="tournaments.html?id=${encodeURIComponent(t.tournamentId)}">${esc(t.name)}</a> ${typeBadge(t.type)}${formatBadge(t.format)}${t.deck ? ` <span class="deck" title="Deck">${esc(t.deck)}</span>` : ""}</span>
      <span class="muted">${esc(t.date)}</span>
      <span>${t.rank ? `#${t.rank} of ${t.playerCount}` : ""}</span>
      <span class="num"><strong>${t.wins}-${t.losses}-${t.draws}</strong>${t.byes ? ` <span class="muted">(+${t.byes} bye)</span>` : ""}</span>
      ${showElo ? `<span class="num" title="Elo change in this tournament">${fmtDelta(t.delta)}</span>` : ""}</summary>
      <table><thead><tr><th class="num">Round</th><th>Opponent</th><th>Result</th><th class="num">Games</th>${showElo ? '<th class="num">Elo change</th>' : ""}</tr></thead><tbody>
      ${t.matches.map((m) => `<tr><td class="num">${m.round}</td><td>${playerLink(m.opponentId, m.opponentName, ladder, format)}</td>
        <td>${resultCell(m.result)}</td><td class="num">${esc(m.score)}</td>${showElo ? `<td class="num">${m.result === "BYE" ? "" : fmtDelta(m.delta)}</td>` : ""}</tr>`).join("")}
      </tbody></table></details>`).join("");
}

async function load() {
  if (!pid) return showError(content, "No player selected");
  content.innerHTML = '<p class="muted">Loading…</p>';
  try {
    const query = { ladder, format };
    const [p, h] = await Promise.all([api(`/players/${pid}`, { query }), api(`/players/${pid}/history`, { query })]);
    const s = p.stats;
    document.title = `${p.displayName} · Delo`;
    content.innerHTML = `
      <h1 class="${p.hidden ? "hidden-name" : ""}">${esc(p.displayName)}${badge(p.membership)}</h1>
      <p class="secondary">${p.provisional ? `Provisional: ${s.matches} of ${p.minMatches} rated matches` : `Rank #${p.rank} of ${p.playerCount}`}
        · ${ladderLabel(ladder, format)} ladder</p>
      <div class="toolbar"><div id="ladder"></div><div id="format"></div></div>
      <div class="tiles">
        ${p.provisional
          ? tile("Elo", '<span class="muted">Provisional</span>', `shown after ${p.minMatches} rated matches, ${p.minMatches - s.matches} to go`)
          : `${tile("Elo", fmtRating(p.rating))}${tile("Peak Elo", fmtRating(s.peakRating), s.peakDate ? esc(s.peakDate) : "")}`}
        ${tile("Win rate", fmtPct(s.winrate), `${s.wins}-${s.losses}-${s.draws} (W-L-D)`)}
        ${tile("Matches", s.matches, `${s.tournamentCount} tournament${s.tournamentCount === 1 ? "" : "s"}`)}
        ${tile("Wins", s.wins)}${tile("Losses", s.losses)}${tile("Draws", s.draws)}
        ${tile("Games", s.games, `${s.gameWins}-${s.gameLosses}-${s.gameDraws} (W-L-D)`)}
        ${tile("Longest win streak", s.longestWinStreak)}
        ${tile("Longest loss streak", s.longestLossStreak)}
        ${matchupTile("Best matchup", s.bestMatchup)}
        ${matchupTile("Worst matchup", s.worstMatchup)}
      </div>
      ${p.provisional ? "" : `<h2>Elo history</h2>
      <div class="card chart-card"><canvas id="elo-chart" role="img" aria-label="Elo rating after each rated match"></canvas></div>
      <details style="margin-top:8px"><summary class="secondary">Show as table</summary>${historyTable(h.history)}</details>`}
      <h2>Tournament history</h2>
      ${tournamentHistory(p.tournaments, !p.provisional) || '<p class="muted">No tournaments.</p>'}`;
    ladderToggle(document.getElementById("ladder"), (l) => { ladder = l; load(); });
    formatSelect(document.getElementById("format"), (f) => { format = f; load(); });
    if (!p.provisional) renderChart(h.history, h.initialRating);
  } catch (err) {
    if (err.status === 404 && err.data?.availableLadders) {
      const other = err.data.availableLadders[0];
      const link = (l, f) => `player.html?${new URLSearchParams({ id: pid, ladder: l, format: f })}`;
      const alternative = other
        ? `<a href="${link(other, format)}">View the ${ladderLabel(other, format)} ladder instead</a>.`
        : format ? `<a href="${link(ladder, "")}">View all formats instead</a>.` : "";
      content.innerHTML = `<div class="toolbar"><div id="ladder"></div><div id="format"></div></div>
        <div class="msg">This player has no rated matches in the ${ladderLabel(ladder, format)} ladder. ${alternative}</div>`;
      ladderToggle(document.getElementById("ladder"), (l) => { ladder = l; load(); });
      formatSelect(document.getElementById("format"), (f) => { format = f; load(); });
    } else {
      showError(content, err);
    }
  }
}

load();
