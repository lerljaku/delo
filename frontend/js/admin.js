import { api, currentUser, esc, renderNav, showError } from "./common.js";

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

async function loadPlayers() {
  const select = document.getElementById("player-select");
  const players = (await api("/leaderboard", { query: { ladder: "all" } })).players;
  select.innerHTML = players.map((p) => `<option value="${esc(p.playerId)}">${esc(p.displayName)} (${esc(p.playerId)})${p.hidden ? " · hidden" : ""}${p.membership ? ` · ${esc(p.membership)}` : ""}</option>`).join("");
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
      <label class="field">Name visibility<select name="hidden"><option value="">Visible</option><option value="1">Hidden</option></select></label>
      <label class="field">Membership<select name="membership"><option value="">None</option><option value="supporter">Supporter</option><option value="diamond">Diamond</option></select></label>
    </div><button class="primary">Save</button><div id="player-msg"></div></form>

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
      <button class="primary" style="margin-top:8px">Publish</button><div id="note-msg"></div></form>`;

  loadClaims();
  loadPlayers().catch((err) => showError(document.getElementById("player-msg"), err));

  document.getElementById("player-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    const msg = document.getElementById("player-msg");
    try {
      await api(`/admin/players/${document.getElementById("player-select").value}`, {
        method: "POST", body: { hidden: !!f.hidden.value, membership: f.membership.value },
      });
      msg.innerHTML = '<div class="msg ok">Saved.</div>';
      loadPlayers();
    } catch (err) { showError(msg, err); }
  });

  document.getElementById("recalc").addEventListener("click", async (e) => {
    const msg = document.getElementById("recalc-msg");
    e.target.disabled = true;
    try {
      const r = await api("/admin/recalculate", { method: "POST", body: {} });
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
