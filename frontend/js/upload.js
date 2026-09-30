import { api, currentUser, esc, renderNav, showError } from "./common.js";

renderNav("upload.html");
const content = document.getElementById("content");

if (!currentUser()?.isAdmin) {
  content.innerHTML = '<div class="msg error">Only admins can upload results. <a href="account.html">Sign in</a> with an admin account.</div>';
} else {
  content.innerHTML = `
    <form id="form" class="card">
      <div class="form-grid">
        <label class="field">Tournament name<input type="text" name="name" required placeholder="FNM Modern"></label>
        <label class="field">Date<input type="date" name="date" required></label>
        <label class="field">Type<select name="type">
          <option value="rel">REL (official, prizes, entry fee)</option>
          <option value="casual">Casual (pub / private games)</option></select></label>
        <label class="field">Format<select name="format">
          <option value="auto">Detect automatically</option><option value="csv">CSV</option>
          <option value="eventlink">Eventlink / Companion (copied pairings)</option><option value="json">JSON</option><option value="text">Simple pairings (Alice vs Bob 2-1)</option></select></label>
      </div>
      <label class="field">Results <input type="file" id="file" accept=".csv,.json,.txt,text/plain"></label>
      <textarea name="content" required placeholder="Round 1&#10;Alice Novak vs Bob Horvath 2-1&#10;Carol Svoboda vs Dave Dvorak 0-2&#10;Eve Cerna - BYE"></textarea>
      <div class="row" style="margin-top:12px"><button type="button" class="secondary" id="preview">Preview</button>
        <button type="submit" class="primary">Upload and recalculate</button></div>
    </form>
    <div id="result"></div>`;

  const form = document.getElementById("form");
  const result = document.getElementById("result");
  form.date.valueAsDate = new Date();
  document.getElementById("file").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (file) form.content.value = await file.text();
  });

  const payload = () => Object.fromEntries(new FormData(form));

  document.getElementById("preview").addEventListener("click", async () => {
    try {
      const r = await api("/tournaments", { method: "POST", body: payload(), query: { dryRun: "1" } });
      const t = r.tournament;
      const name = (id) => esc(t.players[id]);
      result.innerHTML = `
        ${r.duplicate ? '<div class="msg error">This exact tournament has already been uploaded.</div>' : ""}
        <div class="msg ok">Parsed ${esc(t.sourceFormat.toUpperCase())}: ${Object.keys(t.players).length} players,
          ${t.matches.filter((m) => m.p2).length} matches, ${new Set(t.matches.map((m) => m.round)).size} rounds.</div>
        ${r.newPlayers.length ? `<div class="msg"><strong>${r.newPlayers.length} new player(s)</strong>. Check for typos, because a misspelled
          name creates a separate player: ${r.newPlayers.map(esc).join(", ")}</div>` : ""}
        <h2>Standings preview</h2>
        <div class="table-wrap"><table><thead><tr><th class="num">#</th><th>Player</th><th class="num">Points</th><th class="num">W-L-D</th></tr></thead><tbody>
        ${t.standings.map((s) => `<tr><td class="num">${s.rank}</td><td>${name(s.playerId)}</td><td class="num">${s.points}</td>
          <td class="num">${s.wins}-${s.losses}-${s.draws}${s.byes ? ` +${s.byes} bye` : ""}</td></tr>`).join("")}</tbody></table></div>`;
    } catch (err) {
      showError(result, err);
    }
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      const r = await api("/tournaments", { method: "POST", body: payload() });
      result.innerHTML = `<div class="msg ok">Uploaded. Recalculated ${r.recalculation.tournaments} tournaments and ${r.recalculation.players} players.
        <a href="tournaments.html?id=${encodeURIComponent(r.tournamentId)}">View tournament</a></div>`;
      form.content.value = "";
    } catch (err) {
      showError(result, err);
    } finally {
      button.disabled = false;
    }
  });
}
