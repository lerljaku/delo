import { api, currentUser, esc, renderNav, showError } from "./common.js";
import { renderVisitors } from "./visitors.js";

renderNav("admin.html");
const content = document.getElementById("content");

async function loadClaims() {
  const box = document.getElementById("claims");
  try {
    const claims = await api("/admin/claims");
    box.innerHTML = claims.length
      ? `<div class="table-wrap"><table><thead><tr><th>Email</th><th>Player</th><th>Note</th><th>Requested</th><th></th></tr></thead><tbody>
        ${claims.map((c) => `<tr><td>${esc(c.email)}</td><td>${esc(c.playerName)}</td><td>${esc(c.claimNote)}</td><td>${esc(c.claimRequestedAt)}</td>
          <td><button class="primary" data-sub="${esc(c.sub)}" data-approve="1">Approve</button>
              <button class="secondary" data-sub="${esc(c.sub)}" data-approve="">Reject</button></td></tr>`).join("")}
        </tbody></table></div>`
      : '<p class="muted">No pending claims.</p>';
    box.querySelectorAll("button[data-sub]").forEach((b) => b.addEventListener("click", async () => {
      try {
        await api(`/admin/claims/${encodeURIComponent(b.dataset.sub)}`, { method: "POST", body: { approve: !!b.dataset.approve } });
        loadClaims();
      } catch (err) { showError(box, err); }
    }));
  } catch (err) { showError(box, err); }
}

async function loadIslands() {
  const box = document.getElementById("islands");
  try {
    const islands = await api("/admin/islands");
    box.innerHTML = islands.length
      ? `<div class="table-wrap"><table><thead><tr><th>Island</th><th>Id</th><th class="num">Tournaments</th><th></th></tr></thead><tbody>
        ${islands.map((i, n) => `<tr><td>${esc(i.icon || "🏝")} ${esc(i.name)}${n === 0 ? ' <span class="muted small">(default)</span>' : ""}</td>
          <td class="muted">${esc(i.islandId)}</td><td class="num">${i.tournaments}</td>
          <td><button class="link-button rename" data-id="${esc(i.islandId)}" data-name="${esc(i.name)}">Rename</button>
            <button class="link-button change-icon" data-id="${esc(i.islandId)}" data-icon="${esc(i.icon || "")}">Change icon</button></td></tr>`).join("")}
        </tbody></table></div>`
      : '<p class="muted">No islands yet: everything is one group. Run backend/migrate_islands.py to move the existing data into the first island.</p>';
    box.querySelectorAll("button.change-icon").forEach((b) => b.addEventListener("click", async () => {
      const icon = prompt("Icon for the island: an emoji such as a flag (empty: the default 🏝)", b.dataset.icon);
      if (icon === null || icon === b.dataset.icon) return;
      try {
        await api(`/admin/islands/${encodeURIComponent(b.dataset.id)}`, { method: "POST", body: { icon } });
        loadIslands();
      } catch (err) { showError(box, err); }
    }));
    box.querySelectorAll("button.rename").forEach((b) => b.addEventListener("click", async () => {
      const name = prompt("New name for the island", b.dataset.name);
      if (!name || name === b.dataset.name) return;
      try {
        await api(`/admin/islands/${encodeURIComponent(b.dataset.id)}`, { method: "POST", body: { name } }); // icon unchanged
        loadIslands();
      } catch (err) { showError(box, err); }
    }));
  } catch (err) { showError(box, err); }
}

async function loadPlayers() {
  const select = document.getElementById("player-select");
  const players = (await api("/leaderboard", { query: { ladder: "all" } })).players;
  // every player list on this page is alphabetical (accents and case handled by localeCompare)
  const byName = players.slice().sort((a, b) => a.displayName.localeCompare(b.displayName, undefined, { sensitivity: "base" }));
  const options = byName.map((p) => `<option value="${esc(p.playerId)}">${esc(p.displayName)} (${esc(p.playerId)})${p.hidden ? " · hidden" : ""}${p.membership ? ` · ${esc(p.membership)}` : ""}</option>`).join("");
  select.innerHTML = options;
  document.getElementById("erase-select").innerHTML = options;
  // merging: with match counts (a misspelling usually has only a few)
  const mergeOptions = byName.map((p) => `<option value="${esc(p.playerId)}">${esc(p.displayName)} · ${p.summary.matches} match${
    p.summary.matches === 1 ? "" : "es"} (${esc(p.playerId)})</option>`).join("");
  document.getElementById("merge-from").innerHTML = mergeOptions;
  document.getElementById("merge-into").innerHTML = mergeOptions;
}

if (!currentUser()?.isAdmin) {
  content.innerHTML = '<div class="msg error">Admins only.</div>';
} else {
  content.innerHTML = `
    <h2>Profile claims</h2>
    <p class="secondary">Approve only when you have verified the person, for example in person at the store or via the email registered with your tournament organizer.</p>
    <div id="claims"><p class="muted">Loading…</p></div>

    <h2>Player moderation</h2>
    <form id="player-form" class="card"><div class="form-grid">
      <label class="field">Player<select id="player-select"></select></label>
      <label class="field">Name visibility<select name="hidden"><option value="keep">Keep as is</option><option value="">Visible</option><option value="1">Hidden</option></select></label>
      <label class="field">Membership<select name="membership"><option value="keep">Keep as is</option><option value="">None</option><option value="supporter">💎 Supporter (donated, see Support page)</option><option value="diamond">Diamond</option></select></label>
    </div><button class="primary">Save</button><div id="player-msg"></div></form>

    <h2>Merge players</h2>
    <p class="secondary">For a name misspelled in a tournament: the misspelled player becomes the correct one in every tournament
      (results, standings, name), their decks move along, and all ratings are recalculated. Later uploads with the same misspelling
      go to the correct player automatically. Not possible when both played in the same tournament (then they are two people).</p>
    <form id="merge-form" class="card"><div class="form-grid">
      <label class="field">Misspelled player<select id="merge-from"></select></label>
      <label class="field">Merge into (correct player)<select id="merge-into"></select></label></div>
      <div class="row"><button type="button" class="secondary" id="merge-preview">Preview</button>
        <button class="primary" id="merge-go" disabled>Merge</button></div>
      <div id="merge-msg"></div></form>

    <h2>Islands</h2>
    <p class="secondary">An island is a group of players who play each other, usually a country. Each island has its own
      leaderboards, tournaments and upcoming tournaments; ratings are never compared across islands. Uploads go to the island
      selected in the navigation.</p>
    <div id="islands"><p class="muted">Loading…</p></div>
    <form id="island-form" class="card" style="margin-top:12px"><div class="form-grid">
      <label class="field">New island<input type="text" name="name" required maxlength="60" placeholder="e.g. Slovak Island"></label>
      <label class="field">Icon <span class="muted small">(optional emoji, e.g. a flag)</span><input type="text" name="icon" maxlength="16" placeholder="🇸🇰"></label></div>
      <button class="primary">Create island</button><div id="island-msg"></div></form>

    <h2>Erase player (GDPR)</h2>
    <p class="secondary">For removal requests under the <a href="privacy.html">privacy notice</a>. Replaces the player's name with
      “Deleted player” in every tournament, including the original uploads, and in all future uploads. Matches stay, so other
      players' ratings don't change. Unlinks any account. Cannot be undone. Hidden players are listed as “Hidden player”, so
      identify them by id.</p>
    <form id="erase-form" class="card"><div class="form-grid">
      <label class="field">Player<select id="erase-select"></select></label></div>
      <button class="secondary">Erase player</button><div id="erase-msg"></div></form>

    <h2>Recalculate ratings</h2>
    <p class="secondary">Replays every tournament through the current Elo model. Run it after changing the model.</p>
    <button class="secondary" id="recalc">Recalculate everything</button><div id="recalc-msg"></div>

    <h2>Publish release note</h2>
    <form id="note-form" class="card"><div class="form-grid">
      <label class="field">Version<input type="text" name="version" required placeholder="0.2.0"></label>
      <label class="field">Title<input type="text" name="title" required></label>
      <label class="field">Date<input type="date" name="date"></label>
      <label class="field">Summary<input type="text" name="summary"></label></div>
      <label class="field">Body (Markdown)<textarea name="body" required style="min-height:140px"></textarea></label>
      <button class="primary" style="margin-top:8px">Publish</button><div id="note-msg"></div></form>

    <h2>Site visitors</h2>
    <div id="visitors"></div>`;

  loadClaims();
  renderVisitors(document.getElementById("visitors"));
  loadIslands();
  document.getElementById("island-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = document.getElementById("island-msg");
    try {
      const island = await api("/admin/islands", { method: "POST", body: { name: e.target.name.value, icon: e.target.icon.value } });
      msg.innerHTML = `<div class="msg ok">Created ${esc(island.name)}. Select it in the navigation to upload its tournaments.</div>`;
      e.target.reset();
      loadIslands();
    } catch (err) { showError(msg, err); }
  });
  loadPlayers().catch((err) => showError(document.getElementById("player-msg"), err));

  const mergeFrom = document.getElementById("merge-from");
  const mergeInto = document.getElementById("merge-into");
  const mergeGo = document.getElementById("merge-go");
  const mergeMsg = document.getElementById("merge-msg");
  const mergeRequest = (query) => api(`/admin/players/${encodeURIComponent(mergeFrom.value)}/merge`, {
    method: "POST", body: { into: mergeInto.value }, query, background: true, // a preview answers right away
  });
  const listTournaments = (rows) => rows.map((t) => `<li><a href="tournaments.html?id=${encodeURIComponent(t.tournamentId)}">${esc(t.name)}</a>
    <span class="muted">${esc(t.date)}</span></li>`).join("");
  [mergeFrom, mergeInto].forEach((s) => s.addEventListener("change", () => { mergeGo.disabled = true; mergeMsg.innerHTML = ""; }));
  document.getElementById("merge-preview").addEventListener("click", async () => {
    mergeGo.disabled = true;
    try {
      const r = await mergeRequest({ dryRun: "1" });
      mergeMsg.innerHTML = r.conflicts.length
        ? `<div class="msg error">${esc(r.from)} and ${esc(r.into)} both played in these tournaments, so they are two different people:
            <ul>${listTournaments(r.conflicts)}</ul></div>`
        : `<div class="msg"><strong>${esc(r.from)}</strong> → <strong>${esc(r.into)}</strong> in ${r.tournaments.length}
            tournament${r.tournaments.length === 1 ? "" : "s"}:<ul>${listTournaments(r.tournaments)}</ul></div>`;
      mergeGo.disabled = r.conflicts.length > 0;
    } catch (err) { showError(mergeMsg, err); }
  });
  document.getElementById("merge-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const from = mergeFrom.selectedOptions[0]?.textContent, into = mergeInto.selectedOptions[0]?.textContent;
    if (!confirm(`Merge ${from}\ninto ${into}?\n\nThis can't be undone, and all ratings are recalculated.`)) return;
    mergeGo.disabled = true;
    try {
      const r = await mergeRequest();
      mergeMsg.innerHTML = `<div class="msg ok">Merged ${esc(r.from)} into ${esc(r.into)} in ${r.tournaments.length} tournament${
        r.tournaments.length === 1 ? "" : "s"}. Ratings recalculated.</div>`;
      loadPlayers();
    } catch (err) { showError(mergeMsg, err); }
  });

  document.getElementById("player-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    const msg = document.getElementById("player-msg");
    try {
      await api(`/admin/players/${document.getElementById("player-select").value}`, {
        // only what was changed: e.g. making someone a Supporter must not un-hide their name
        method: "POST", body: {
          ...(f.hidden.value === "keep" ? {} : { hidden: !!f.hidden.value }),
          ...(f.membership.value === "keep" ? {} : { membership: f.membership.value }),
        },
      });
      msg.innerHTML = '<div class="msg ok">Saved.</div>';
      loadPlayers();
    } catch (err) { showError(msg, err); }
  });

  document.getElementById("erase-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const select = document.getElementById("erase-select");
    const msg = document.getElementById("erase-msg");
    const label = select.selectedOptions[0]?.textContent;
    if (!label || !confirm(`Erase ${label}? Their name is removed from all results for good.`)) return;
    try {
      const r = await api(`/admin/players/${select.value}/erase`, { method: "POST", body: {}, background: true });
      msg.innerHTML = `<div class="msg ok">Erased from ${r.tournaments} tournament(s).</div>`;
      loadPlayers();
    } catch (err) { showError(msg, err); }
  });

  document.getElementById("recalc").addEventListener("click", async (e) => {
    const msg = document.getElementById("recalc-msg");
    e.target.disabled = true;
    msg.innerHTML = '<div class="msg">Recalculating… this can take a few minutes.</div>';
    try {
      const r = await api("/admin/recalculate", { method: "POST", body: {}, background: true });
      msg.innerHTML = `<div class="msg ok">Recalculated ${r.tournaments} tournaments, ${r.players} players (model v${esc(r.modelVersion)}).</div>`;
    } catch (err) { showError(msg, err); } finally { e.target.disabled = false; }
  });

  document.getElementById("note-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = document.getElementById("note-msg");
    try {
      await api("/release-notes", { method: "POST", body: Object.fromEntries(new FormData(e.target)) });
      msg.innerHTML = '<div class="msg ok">Published. <a href="docs.html">View docs</a></div>';
      e.target.reset();
    } catch (err) { showError(msg, err); }
  });
}
