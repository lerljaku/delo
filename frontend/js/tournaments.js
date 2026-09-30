import { api, currentUser, esc, param, playerLink, renderNav, showError, typeBadge } from "./common.js";

renderNav("tournaments.html");
const content = document.getElementById("content");
const tid = param("id");
const ladderFor = (type) => (type === "rel" ? "rel" : "all");

async function list() {
  const rows = await api("/tournaments");
  content.innerHTML = `<h1>Tournaments</h1>
    ${rows.length ? `<div class="table-wrap"><table><thead><tr><th>Date</th><th>Name</th><th>Type</th><th class="num">Players</th><th class="num">Matches</th></tr></thead>
    <tbody>${rows.map((t) => `<tr><td>${esc(t.date)}</td><td><a href="tournaments.html?id=${encodeURIComponent(t.tournamentId)}">${esc(t.name)}</a></td>
      <td>${typeBadge(t.type)}</td><td class="num">${t.playerCount}</td><td class="num">${t.matchCount}</td></tr>`).join("")}</tbody></table></div>`
      : '<div class="msg">No tournaments uploaded yet.</div>'}`;
}

async function detail() {
  const t = await api(`/tournaments/${encodeURIComponent(tid)}`);
  const ladder = ladderFor(t.type);
  const name = (id) => t.players[id];
  const rounds = [...new Set(t.matches.map((m) => m.round))];
  document.title = `${t.name} · ESL`;
  content.innerHTML = `
    <p><a href="tournaments.html">← All tournaments</a></p>
    <h1>${esc(t.name)} ${typeBadge(t.type)}</h1>
    <p class="secondary">${esc(t.date)} · ${Object.keys(t.players).length} players · uploaded ${esc(t.uploadedAt)}</p>
    <h2>Standings</h2>
    <div class="table-wrap"><table><thead><tr><th class="num">#</th><th>Player</th><th class="num">Points</th><th class="num">W-L-D</th>
      <th class="num" title="Opponents' match-win %">OMW%</th><th class="num" title="Game-win %">GW%</th></tr></thead><tbody>
      ${t.standings.map((s) => `<tr><td class="num">${s.rank}</td><td>${playerLink(s.playerId, name(s.playerId), ladder)}</td>
        <td class="num"><strong>${s.points}</strong></td><td class="num">${s.wins}-${s.losses}-${s.draws}${s.byes ? ` <span class="muted">+${s.byes} bye</span>` : ""}</td>
        <td class="num">${(s.omw * 100).toFixed(1)}</td><td class="num">${(s.gw * 100).toFixed(1)}</td></tr>`).join("")}
    </tbody></table></div>
    ${rounds.map((r) => `<h2>Round ${r}</h2><div class="table-wrap"><table><tbody>
      ${t.matches.filter((m) => m.round === r).map((m) => `<tr><td>${playerLink(m.p1, name(m.p1), ladder)}</td>
        <td class="num">${m.p2 ? `${m.p1Wins}-${m.p2Wins}${m.draws ? `-${m.draws}` : ""}` : ""}</td>
        <td>${m.p2 ? playerLink(m.p2, name(m.p2), ladder) : '<span class="muted">BYE</span>'}</td></tr>`).join("")}
    </tbody></table></div>`).join("")}
    ${currentUser()?.isAdmin ? '<h2>Admin</h2><button class="secondary" id="delete">Delete tournament and recalculate</button><div id="admin-msg"></div>' : ""}`;

  document.getElementById("delete")?.addEventListener("click", async () => {
    if (!confirm(`Delete "${t.name}"? All ratings will be recalculated without it.`)) return;
    try {
      await api(`/tournaments/${encodeURIComponent(tid)}`, { method: "DELETE" });
      location.href = "tournaments.html";
    } catch (err) {
      showError(document.getElementById("admin-msg"), err);
    }
  });
}

(tid ? detail() : list()).catch((err) => showError(content, err));
