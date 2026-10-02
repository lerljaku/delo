import {
  api, badge, esc, fmtPct, fmtRating, formatSelect, getFormat, getLadder, HIDDEN_NAME, ladderToggle, playerLink, renderNav, showError,
} from "./common.js";

renderNav("index.html");
const content = document.getElementById("content");
const search = document.getElementById("search");
let rows = [];
let ladder = getLadder();
let format = getFormat();
let minMatches = 1;

// key: [header, value to sort by, first direction when clicked (1 = ascending)]
const COLUMNS = {
  rank: ["#", (r) => r.rank, 1],
  name: ["Player", (r) => r.displayName.toLowerCase(), 1],
  rating: ["Elo", (r) => r.rating, -1],
  peak: ["Peak", (r) => r.summary.peakRating, -1],
  record: ["W-L-D", (r) => r.summary.wins - r.summary.losses / 1000, -1],
  matches: ["Matches", (r) => r.summary.matches, -1],
  winrate: ["Win rate", (r) => r.summary.winrate, -1],
};
let sort = { key: "rank", dir: 1 };

function sorted(list) {
  const value = COLUMNS[sort.key][1];
  return list.slice().sort((a, b) => {
    const x = value(a), y = value(b);
    return (x < y ? -1 : x > y ? 1 : 0) * sort.dir || a.rank - b.rank;
  });
}

function header(key) {
  const [label] = COLUMNS[key];
  const cls = key === "name" ? "" : ' class="num"';
  const active = sort.key === key;
  const ariaSort = active ? (sort.dir === 1 ? "ascending" : "descending") : "none";
  const arrow = active ? (sort.dir === 1 ? "▲" : "▼") : "";
  return `<th${cls} aria-sort="${ariaSort}"><button class="sort" data-key="${key}">${label}<span class="arrow" aria-hidden="true">${arrow}</span></button></th>`;
}

function rankedTable(list) {
  if (!list.length) return `<div class="msg">Nobody has ${minMatches} rated matches ${format ? "in this format " : ""}yet, so there is no ranking.</div>`;
  return `<div class="table-wrap"><table>
    <thead><tr>${Object.keys(COLUMNS).map(header).join("")}</tr></thead>
    <tbody>${list.map((r) => `<tr>
      <td class="num">${r.rank}</td>
      <td>${playerLink(r.playerId, r.displayName, ladder, format)}${badge(r.membership)}</td>
      <td class="num"><strong>${fmtRating(r.rating)}</strong></td>
      <td class="num muted">${fmtRating(r.summary.peakRating)}</td>
      <td class="num">${r.summary.wins}-${r.summary.losses}-${r.summary.draws}</td>
      <td class="num">${r.summary.matches}</td>
      <td class="num">${fmtPct(r.summary.winrate)}</td></tr>`).join("")}
    </tbody></table></div>`;
}

// Fewer than minMatches rated matches: no rank and no Elo yet (the API leaves them out).
function provisionalTable(list) {
  if (!list.length) return "";
  return `<details class="provisional"><summary><h2>Provisional players (${list.length})</h2></summary>
    <p class="secondary">Players get a rank and an Elo after ${minMatches} rated matches. Byes don't count.</p>
    <div class="table-wrap"><table><thead><tr><th>Player</th><th class="num">Rated matches</th><th class="num">W-L-D</th><th class="num">Win rate</th></tr></thead>
    <tbody>${list.map((r) => `<tr><td>${playerLink(r.playerId, r.displayName, ladder, format)}${badge(r.membership)}</td>
      <td class="num">${r.summary.matches} / ${minMatches}</td><td class="num">${r.summary.wins}-${r.summary.losses}-${r.summary.draws}</td>
      <td class="num">${fmtPct(r.summary.winrate)}</td></tr>`).join("")}</tbody></table></div></details>`;
}

function render() {
  const q = search.value.trim().toLowerCase();
  const visible = rows.filter((r) => !q || (r.displayName !== HIDDEN_NAME && r.displayName.toLowerCase().includes(q)));
  if (!rows.length) {
    content.innerHTML = `<div class="msg">No rated players ${format ? "in this format " : ""}yet. An admin needs to upload tournament results.</div>`;
    return;
  }
  const provisional = visible.filter((r) => r.provisional);
  content.innerHTML = `${rankedTable(sorted(visible.filter((r) => !r.provisional)))}${provisionalTable(provisional)}
    ${visible.length ? "" : `<p class="muted">No player matches “${esc(search.value)}”.</p>`}`;
  if (q && provisional.length) content.querySelector("details.provisional").open = true;
  content.querySelectorAll("button.sort").forEach((b) => b.addEventListener("click", () => {
    const key = b.dataset.key;
    sort = sort.key === key ? { key, dir: -sort.dir } : { key, dir: COLUMNS[key][2] };
    render();
    content.querySelector(`button.sort[data-key="${key}"]`).focus();
  }));
}

async function load() {
  content.innerHTML = '<p class="muted">Loading…</p>';
  try {
    const board = await api("/leaderboard", { query: { ladder, format } });
    rows = board.players;
    minMatches = board.minMatches;
    render();
  } catch (err) {
    showError(content, err);
  }
}

ladderToggle(document.getElementById("ladder"), (l) => { ladder = l; load(); });
formatSelect(document.getElementById("format"), (f) => { format = f; load(); });
search.addEventListener("input", render);
load();
