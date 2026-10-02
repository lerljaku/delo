import { api, currentUser, esc, externalLink, GAME_FORMATS, renderNav, showError } from "./common.js";

renderNav("upload.html");
const content = document.getElementById("content");

// Example input per file format, shown as the textarea placeholder.
const EXAMPLES = {
  auto: `Paste results in any supported format (see Docs), e.g.

Round 1
Alice Novak vs Bob Horvath 2-1
Carol Svoboda vs Dave Dvorak 0-2
Eve Cerna - BYE`,
  eventlink: `Paste the pairings copied from Eventlink / Companion, all rounds, e.g.

1
Alice Novak
1–0–0
21
Bob Horvath
0–1–0
2
Carol Svoboda
0–1–0
02
Dave Dvorak
1–0–0

Or pick several files above, e.g. one export per round.`,
  csv: `round,player1,player2,player1_wins,player2_wins,draws
1,Alice Novak,Bob Horvath,2,1,0
1,Carol Svoboda,Dave Dvorak,0,2,0
1,Eve Cerna,BYE,2,0,0`,
  json: `{"rounds": [{"round": 1, "matches": [
  {"player1": "Alice Novak", "player2": "Bob Horvath", "result": "2-1"},
  {"player1": "Eve Cerna", "player2": "BYE", "result": "2-0"}
]}]}`,
  text: `Round 1
Alice Novak vs Bob Horvath 2-1
Carol Svoboda vs Dave Dvorak 0-2
Eve Cerna - BYE
Round 2
Alice Novak vs Dave Dvorak 1-1-1`,
};

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
        <label class="field">Format<select name="format" required><option value="">Choose…</option>
          ${Object.entries(GAME_FORMATS).map(([id, label]) => `<option value="${id}">${label}</option>`).join("")}</select></label>
        <label class="field">Link (optional)<input type="url" name="link" placeholder="https://… event page, e.g. Eventlink or store post"
          title="Public page of the event. Helps others verify the tournament really happened."></label>
        <label class="field">File format<select name="sourceFormat">
          <option value="auto">Detect automatically</option><option value="csv">CSV</option>
          <option value="eventlink">Eventlink / Companion (copied pairings)</option><option value="json">JSON</option><option value="text">Simple pairings (Alice vs Bob 2-1)</option></select></label>
      </div>
      <label class="field">Results <input type="file" id="file" multiple accept=".csv,.json,.txt,text/plain"
        title="Pick one file, or several Eventlink exports of the same event (e.g. one per round)"></label>
      <div id="files" class="msg" hidden></div>
      <textarea name="content" required></textarea>
      <div class="row" style="margin-top:12px"><button type="button" class="secondary" id="preview">Preview</button>
        <button type="submit" class="primary">Upload and recalculate</button></div>
    </form>
    <div id="result"></div>`;

  const form = document.getElementById("form");
  const result = document.getElementById("result");
  const fileInput = document.getElementById("file");
  const filesBox = document.getElementById("files");
  let files = null; // several picked files: [{name, content}], combined by the server in round order
  form.date.valueAsDate = new Date();
  const setPlaceholder = () => { form.content.placeholder = EXAMPLES[form.sourceFormat.value] || EXAMPLES.auto; };
  form.sourceFormat.addEventListener("change", setPlaceholder);
  setPlaceholder();

  function showFiles(list) {
    files = list;
    form.content.hidden = form.content.disabled = !!list; // disabled: not required, not submitted
    filesBox.hidden = !list;
    if (!list) return;
    filesBox.innerHTML = `<strong>${list.length} files</strong> will be combined into one tournament, in round order
      (taken from the W–L–D records, so the order you picked them in doesn't matter): ${list.map((f) => esc(f.name)).join(", ")}.
      Only Eventlink / Companion pairings can be combined. <a href="#" id="clear-files">Clear</a>`;
    filesBox.querySelector("#clear-files").addEventListener("click", (e) => { e.preventDefault(); fileInput.value = ""; showFiles(null); });
  }

  fileInput.addEventListener("change", async (e) => {
    const picked = [...e.target.files];
    if (picked.length > 1) {
      showFiles(await Promise.all(picked.map(async (f) => ({ name: f.name, content: await f.text() }))));
    } else {
      showFiles(null);
      if (picked.length) form.content.value = await picked[0].text();
    }
  });

  const payload = () => ({ ...Object.fromEntries(new FormData(form)), ...(files ? { files } : {}) });
  const sameLinkWarning = (same) => (same?.length ? `<div class="msg error">The link is already used by
    ${same.map((t) => `<a href="tournaments.html?id=${encodeURIComponent(t.tournamentId)}">${esc(t.name)}</a> (${esc(t.date)})`).join(", ")}.
    Check that this is not the same event uploaded again. ${externalLink(form.link.value, "Open link")}</div>` : "");

  document.getElementById("preview").addEventListener("click", async () => {
    try {
      const r = await api("/tournaments", { method: "POST", body: payload(), query: { dryRun: "1" } });
      const t = r.tournament;
      const name = (id) => esc(t.players[id]);
      result.innerHTML = `
        ${r.duplicate ? '<div class="msg error">This exact tournament has already been uploaded.</div>' : ""}
        ${sameLinkWarning(r.sameLink)}
        ${r.erasedPlayers ? `<div class="msg">${r.erasedPlayers} player(s) asked to have their data erased and will appear as “Deleted player”.</div>` : ""}
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
      const rc = r.recalculation;
      const how = rc.mode === "incremental"
        ? `Updated the ratings of ${rc.players} players.`
        : `Recalculated all ${rc.tournaments} tournaments and ${rc.players} players (the tournament is dated before others, or the stored ratings needed a full recalculation).`;
      result.innerHTML = `<div class="msg ok">Uploaded. ${how}
        <a href="tournaments.html?id=${encodeURIComponent(r.tournamentId)}">View tournament</a></div>${sameLinkWarning(r.sameLink)}`;
      form.content.value = "";
      form.link.value = "";
      fileInput.value = "";
      showFiles(null);
    } catch (err) {
      showError(result, err);
    } finally {
      button.disabled = false;
    }
  });
}
