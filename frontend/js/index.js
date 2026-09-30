import { api, badge, esc, fmtPct, fmtRating, getLadder, HIDDEN_NAME, ladderToggle, playerLink, renderNav, showError } from "./common.js";

renderNav("index.html");
const content = document.getElementById("content");
const search = document.getElementById("search");
let rows = [];
let ladder = getLadder();

function render() {
  const q = search.value.trim().toLowerCase();
  const visible = rows.filter((r) => !q || (r.displayName !== HIDDEN_NAME && r.displayName.toLowerCase().includes(q)));
  if (!rows.length) {
    content.innerHTML = '<div class="msg">No rated players yet. An admin needs to upload tournament results.</div>';
    return;
  }
  content.innerHTML = `<div class="table-wrap"><table>
    <thead><tr><th class="num">#</th><th>Player</th><th class="num">Elo</th><th class="num">Peak</th>
      <th class="num">W-L-D</th><th class="num">Matches</th><th class="num">Win rate</th></tr></thead>
    <tbody>${visible.map((r) => `<tr>
      <td class="num">${r.rank}</td>
      <td>${playerLink(r.playerId, r.displayName, ladder)}${badge(r.membership)}</td>
      <td class="num"><strong>${fmtRating(r.rating)}</strong></td>
      <td class="num muted">${fmtRating(r.summary.peakRating)}</td>
      <td class="num">${r.summary.wins}-${r.summary.losses}-${r.summary.draws}</td>
      <td class="num">${r.summary.matches}</td>
      <td class="num">${fmtPct(r.summary.winrate)}</td></tr>`).join("")}
    </tbody></table></div>
    ${visible.length ? "" : `<p class="muted">No player matches “${esc(search.value)}”.</p>`}`;
}

async function load() {
  content.innerHTML = '<p class="muted">Loading…</p>';
  try {
    rows = (await api("/leaderboard", { query: { ladder } })).players;
    render();
  } catch (err) {
    showError(content, err);
  }
}

ladderToggle(document.getElementById("ladder"), (l) => { ladder = l; load(); });
search.addEventListener("input", render);
load();
