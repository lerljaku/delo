import { api, CONFIG, esc, markdown, renderNav, showError } from "./common.js";

renderNav("docs.html");
const content = document.getElementById("content");
const repo = CONFIG.githubRepo || "#";

content.innerHTML = `
  <div class="tiles">
    <div class="tile"><div class="label">Found a bug?</div><div class="value" style="font-size:1rem"><a href="${esc(repo)}/issues/new?labels=bug" rel="noopener">Report a bug on GitHub</a></div></div>
    <div class="tile"><div class="label">Have an idea?</div><div class="value" style="font-size:1rem"><a href="${esc(repo)}/issues/new?labels=enhancement" rel="noopener">Request a feature</a></div></div>
    <div class="tile"><div class="label">Wrong result or name?</div><div class="value" style="font-size:1rem"><a href="${esc(repo)}/issues/new?labels=data" rel="noopener">Report a data problem</a></div></div>
    <div class="tile"><div class="label">Ratings</div><div class="value" style="font-size:1rem"><a href="elo.html">How Elo is calculated</a></div></div>
  </div>

  <h2 id="formats">Upload formats</h2>
  <div class="card markdown">
    <p><strong>Eventlink / Companion</strong>: copy all rounds of the pairings and paste them as they are, one value per line
      (table, player, record, game score, opponent, record). Byes, playoffs and missing table numbers are handled.
      Paste one event per upload. If you exported each round separately, pick all the files at once on the upload page.
      They are combined in round order.</p>
    <pre>1
Alice Novak
1–0–0
21
Bob Horvath
0–1–0</pre>
    <p><strong>CSV</strong>: header row, one match per line. Leave <code>player2</code> empty or write <code>BYE</code> for a bye.</p>
    <pre>round,player1,player2,player1_wins,player2_wins,draws
1,Alice Novak,Bob Horvath,2,1,0
1,Eve Cerna,BYE,2,0,0</pre>
    <p>Instead of the three number columns, you can use one <code>result</code> column such as <code>2-1-0</code>.</p>
    <p><strong>Simple pairings</strong>: optional <code>Round N</code> headers. Without headers, rounds are inferred.</p>
    <pre>Round 1
Alice Novak vs Bob Horvath 2-1
Carol Svoboda vs Dave Dvorak 1-1-1
Eve Cerna - BYE</pre>
    <p><strong>JSON</strong>:</p>
    <pre>{"name": "FNM", "date": "2026-09-12", "type": "rel", "format": "modern",
 "link": "https://example.com/fnm-results",
 "rounds": [{"round": 1, "matches": [{"player1": "Alice", "player2": "Bob", "result": "2-1"}]}]}</pre>
    <p>Every tournament has a format (Modern, Limited, Duel Commander, EDH, Legacy, Vintage or Premodern) and can have a link to its
      public page, such as the Eventlink event or the store's results post, so anyone can check that it really took place.</p>
    <p>Player names must be spelled the same way in every tournament. Capital letters, accents (Jiri = Jiří), emoji and extra spaces are ignored.</p>
  </div>

  <h2>Release notes</h2>
  <div id="notes"><p class="muted">Loading…</p></div>`;

(async () => {
  const box = document.getElementById("notes");
  try {
    const notes = await api("/release-notes");
    if (!notes.length) { box.innerHTML = '<p class="muted">No release notes yet.</p>'; return; }
    const full = await Promise.all(notes.map((n) => api(`/release-notes/${encodeURIComponent(n.version)}`)));
    box.innerHTML = full.map((n) => `<div class="card markdown" style="margin-bottom:12px">
      <h3 style="margin:0">v${esc(n.version)}: ${esc(n.title)} <span class="muted" style="font-weight:400">${esc(n.date)}</span></h3>
      ${markdown(n.body)}</div>`).join("");
  } catch (err) { showError(box, err); }
})();
