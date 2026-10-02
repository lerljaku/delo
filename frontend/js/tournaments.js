import {
  api, currentUser, esc, externalLink, fmtDelta, fmtRating, formatBadge, GAME_FORMATS, ladderLabel, param, playerLink, renderNav, showError,
  typeBadge,
} from "./common.js";

renderNav("tournaments.html");
const content = document.getElementById("content");
const tid = param("id");
const ladderFor = (type) => (type === "rel" ? "rel" : "all");

async function list() {
  const rows = await api("/tournaments");
  content.innerHTML = `<h1>Tournaments</h1>
    ${rows.length ? `<div class="table-wrap"><table><thead><tr><th>Date</th><th>Name</th><th>Type</th><th>Format</th><th class="num">Players</th><th class="num">Matches</th><th>Link</th></tr></thead>
    <tbody>${rows.map((t) => `<tr><td>${esc(t.date)}</td><td><a href="tournaments.html?id=${encodeURIComponent(t.tournamentId)}">${esc(t.name)}</a></td>
      <td>${typeBadge(t.type)}</td><td>${t.format ? esc(GAME_FORMATS[t.format] || t.format) : '<span class="muted">—</span>'}</td>
      <td class="num">${t.playerCount}</td><td class="num">${t.matchCount}</td><td>${externalLink(t.link, "Event page ↗")}</td></tr>`).join("")}</tbody></table></div>`
      : '<div class="msg">No tournaments uploaded yet.</div>'}`;
}

function adminForm(t) {
  return `<h2>Admin</h2>
    <form id="edit" class="card"><div class="form-grid">
      <label class="field">Format<select name="format"><option value="">None (counts for all formats only)</option>
        ${Object.entries(GAME_FORMATS).map(([id, label]) => `<option value="${id}" ${id === t.format ? "selected" : ""}>${label}</option>`).join("")}</select></label>
      <label class="field">Link<input type="url" name="link" value="${esc(t.link || "")}" placeholder="https://…"></label></div>
      <div class="row"><button type="submit" class="primary">Save</button>
        <button type="button" class="secondary" id="delete">Delete tournament and recalculate</button></div></form>
    <div id="admin-msg"></div>`;
}

async function detail() {
  const t = await api(`/tournaments/${encodeURIComponent(tid)}`);
  const ladder = ladderFor(t.type);
  const format = t.format || "";
  const name = (id) => t.players[id];
  const change = (id) => {
    const c = t.ratingChanges?.[id];
    return c ? ` <span class="elo-change" title="Elo ${fmtRating(c.ratingBefore)} → ${fmtRating(c.ratingAfter)}">${fmtDelta(c.delta)}</span>` : "";
  };
  const deck = (id) => (t.decks?.[id] ? ` <span class="deck" title="Deck">${esc(t.decks[id])}</span>` : "");
  const rounds = [...new Set(t.matches.map((m) => m.round))];
  document.title = `${t.name} · Delo`;
  content.innerHTML = `
    <p><a href="tournaments.html">← All tournaments</a></p>
    <h1>${esc(t.name)} ${typeBadge(t.type)}${formatBadge(t.format)}</h1>
    <p class="secondary">${esc(t.date)} · ${Object.keys(t.players).length} players · uploaded ${esc(t.uploadedAt)}
      ${t.link ? ` · ${externalLink(t.link, "Event page ↗")}` : ""}</p>
    <h2>Standings</h2>
    <p class="muted">Elo change next to each name is for the ${ladderLabel(t.ratingLadder.ladder, t.ratingLadder.format)} ladder.
      ${currentUser() ? 'Played here? Set your deck on your <a href="account.html#decks">account page</a>.' : ""}</p>
    <div class="table-wrap"><table><thead><tr><th class="num">#</th><th>Player</th><th class="num">Points</th><th class="num">W-L-D</th>
      <th class="num" title="Opponents' match-win %">OMW%</th><th class="num" title="Game-win %">GW%</th></tr></thead><tbody>
      ${t.standings.map((s) => `<tr><td class="num">${s.rank}</td><td>${playerLink(s.playerId, name(s.playerId), ladder, format)}${change(s.playerId)}${deck(s.playerId)}</td>
        <td class="num"><strong>${s.points}</strong></td><td class="num">${s.wins}-${s.losses}-${s.draws}${s.byes ? ` <span class="muted">+${s.byes} bye</span>` : ""}</td>
        <td class="num">${(s.omw * 100).toFixed(1)}</td><td class="num">${(s.gw * 100).toFixed(1)}</td></tr>`).join("")}
    </tbody></table></div>
    ${rounds.map((r) => `<h2>Round ${r}</h2><div class="table-wrap"><table><tbody>
      ${t.matches.filter((m) => m.round === r).map((m) => `<tr><td>${playerLink(m.p1, name(m.p1), ladder, format)}</td>
        <td class="num">${m.p2 ? `${m.p1Wins}-${m.p2Wins}${m.draws ? `-${m.draws}` : ""}` : ""}</td>
        <td>${m.p2 ? playerLink(m.p2, name(m.p2), ladder, format) : '<span class="muted">BYE</span>'}</td></tr>`).join("")}
    </tbody></table></div>`).join("")}
    ${currentUser()?.isAdmin ? adminForm(t) : ""}`;

  const msg = document.getElementById("admin-msg");
  document.getElementById("edit")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    form.querySelector("button[type=submit]").disabled = true;
    try {
      await api(`/tournaments/${encodeURIComponent(tid)}`, { method: "POST", body: Object.fromEntries(new FormData(form)) });
      await detail();
    } catch (err) {
      showError(msg, err);
      form.querySelector("button[type=submit]").disabled = false;
    }
  });
  document.getElementById("delete")?.addEventListener("click", async () => {
    if (!confirm(`Delete "${t.name}"? All ratings will be recalculated without it.`)) return;
    try {
      await api(`/tournaments/${encodeURIComponent(tid)}`, { method: "DELETE" });
      location.href = "tournaments.html";
    } catch (err) {
      showError(msg, err);
    }
  });
}

(tid ? detail() : list()).catch((err) => showError(content, err));
