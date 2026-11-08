import {
  api, currentUser, esc, externalLink, fmtDelta, fmtRating, formatBadge, GAME_FORMATS, ladderLabel, param, playerLink, renderNav, showError,
  typeBadge,
} from "./common.js";
import { deckLabel } from "./decks.js";

renderNav("tournaments.html");
const content = document.getElementById("content");
const tid = param("id");
const ladderFor = (type) => (type === "rel" ? "rel" : "all");

// Deleting removes the tournament's matches from every rating, so everything is recalculated.
async function deleteTournament(t) {
  if (!confirm(`Delete "${t.name}" (${t.date})?

Its results are removed and every player's Elo is recalculated without it. `
    + "To fix wrong results instead, open the tournament and use \"Upload corrected results\".")) return false;
  await api(`/tournaments/${encodeURIComponent(t.tournamentId)}`, { method: "DELETE", background: true });
  return true;
}

// ?format=<id> filters the list ("none": tournaments without a format). Kept in the URL so a
// filtered list can be shared; independent of the leaderboard's saved format.
async function list() {
  const rows = await api("/tournaments");
  const admin = currentUser()?.isAdmin;
  const counts = rows.reduce((c, t) => ({ ...c, [t.format || "none"]: (c[t.format || "none"] || 0) + 1 }), {});
  let filter = param("format") || "";
  if (filter && !counts[filter]) filter = "";
  content.innerHTML = `<h1>Tournaments</h1><div id="list-msg"></div>
    ${rows.length ? `<div class="toolbar"><label class="row">Format <select id="format-filter" aria-label="Filter by format">
      <option value="">All formats (${rows.length})</option>
      ${Object.entries(GAME_FORMATS).filter(([id]) => counts[id]).map(([id, label]) => `<option value="${id}">${label} (${counts[id]})</option>`).join("")}
      ${counts.none ? `<option value="none">Format not set (${counts.none})</option>` : ""}</select></label></div>` : ""}
    <div id="tournament-list"></div>`;
  const select = document.getElementById("format-filter");
  if (select) {
    select.value = filter;
    select.addEventListener("change", () => {
      filter = select.value;
      const url = new URL(location.href);
      if (filter) url.searchParams.set("format", filter); else url.searchParams.delete("format");
      history.replaceState(null, "", url);
      renderRows();
    });
  }

  function renderRows() {
    const box = document.getElementById("tournament-list");
    const shown = rows.filter((t) => !filter || (t.format || "none") === filter);
    box.innerHTML = rows.length ? `<div class="table-wrap"><table><thead><tr><th>Date</th><th>Name</th><th>Type</th><th>Format</th><th class="num">Players</th><th class="num">Matches</th><th>Link</th>${admin ? "<th></th>" : ""}</tr></thead>
    <tbody>${shown.map((t) => `<tr><td>${esc(t.date)}</td><td><a href="tournaments.html?id=${encodeURIComponent(t.tournamentId)}">${esc(t.name)}</a></td>
      <td>${typeBadge(t.type)}</td><td>${t.format ? esc(GAME_FORMATS[t.format] || t.format) : '<span class="muted">—</span>'}</td>
      <td class="num">${t.playerCount}</td><td class="num">${t.matchCount}</td><td>${externalLink(t.link, "Event page ↗")}</td>
      ${admin ? `<td><button class="link-button" data-delete="${esc(t.tournamentId)}" title="Delete and recalculate ratings">Delete</button></td>` : ""}</tr>`).join("")}</tbody></table></div>`
      : '<div class="msg">No tournaments uploaded yet.</div>';
    box.querySelectorAll("button[data-delete]").forEach((b) => b.addEventListener("click", async () => {
      const t = rows.find((r) => r.tournamentId === b.dataset.delete);
      b.disabled = true;
      b.textContent = "Deleting and recalculating…";
      try {
        if (await deleteTournament(t)) await list();
        else { b.disabled = false; b.textContent = "Delete"; }
      } catch (err) {
        showError(document.getElementById("list-msg"), err);
        b.disabled = false;
        b.textContent = "Delete";
      }
    }));
  }
  renderRows();
}

function adminForm(t) {
  return `<h2>Admin</h2>
    <form id="edit" class="card"><div class="form-grid">
      <label class="field">Format<select name="format"><option value="">None (counts for all formats only)</option>
        ${Object.entries(GAME_FORMATS).map(([id, label]) => `<option value="${id}" ${id === t.format ? "selected" : ""}>${label}</option>`).join("")}</select></label>
      <label class="field">Link<input type="url" name="link" value="${esc(t.link || "")}" placeholder="https://…"></label>
      <label class="field" id="island-field" hidden>Island <span class="muted small">(moving recalculates ratings)</span>
        <select name="island" id="island-move" disabled></select></label></div>
      <div class="row"><button type="submit" class="primary">Save</button></div></form>
    <div class="card" style="margin-top:12px">
      <p class="secondary">Wrong results? Upload the corrected file: it replaces this tournament (players' decks are kept) and recalculates
        all ratings. Fake tournament? Delete it: its matches are removed from every rating.</p>
      <div class="row"><a class="button" href="upload.html?replace=${encodeURIComponent(t.id)}">Upload corrected results</a>
        <button type="button" class="secondary danger" id="delete">Delete tournament</button></div></div>
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
  const deck = (id) => (t.decks?.[id] ? ` ${deckLabel(t.decks[id])}` : "");
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
  if (currentUser()?.isAdmin) {
    api(`/admin/tournaments/${encodeURIComponent(tid)}`).then((info) => {
      document.getElementById("edit")?.insertAdjacentHTML("afterbegin",
        `<p class="secondary">Uploaded by <strong>${esc(info.uploadedBy || "unknown")}</strong> · ${esc(info.uploadedAt || "")}</p>`);
    }).catch(() => {});
  }
  const islandMove = document.getElementById("island-move");
  if (islandMove) {
    api("/islands").then((islands) => {
      if (islands.length < 2 && islands.some((i) => i.islandId === t.island)) return; // nowhere to move it
      islandMove.innerHTML = islands.map((i) => `<option value="${esc(i.islandId)}" ${i.islandId === t.island ? "selected" : ""}>${esc(i.name)}</option>`).join("");
      islandMove.disabled = false; // only sent once shown
      document.getElementById("island-field").hidden = false;
    }).catch(() => {});
  }
  document.getElementById("edit")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    form.querySelector("button[type=submit]").disabled = true;
    try {
      await api(`/tournaments/${encodeURIComponent(tid)}`, { method: "POST", body: Object.fromEntries(new FormData(form)), background: true });
      await detail();
    } catch (err) {
      showError(msg, err);
      form.querySelector("button[type=submit]").disabled = false;
    }
  });
  document.getElementById("delete")?.addEventListener("click", async (e) => {
    try {
      e.target.disabled = true;
      if (await deleteTournament({ tournamentId: tid, name: t.name, date: t.date })) location.href = "tournaments.html";
      else e.target.disabled = false;
    } catch (err) {
      showError(msg, err);
      e.target.disabled = false;
    }
  });
}

(tid ? detail() : list()).catch((err) => showError(content, err));
