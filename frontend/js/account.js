import { api, badge, CONFIG, currentUser, esc, GAME_FORMATS, login, logout, playerLink, renderNav, showError } from "./common.js";

renderNav("account.html");
const content = document.getElementById("content");

const STATUS = {
  none: "You haven't claimed a player profile yet.",
  pending: "Your claim is waiting for an admin to verify it.",
  approved: "Verified. This player profile is yours.",
  rejected: "Your last claim was rejected. Contact an admin if this is a mistake.",
};

// One suggestion list per format; the server orders it (own decks, newest first, then others).
async function renderDecks() {
  const box = document.getElementById("decks-box");
  const data = await api("/me/decks");
  if (!data.tournaments.length) {
    box.innerHTML = '<p class="muted">No tournaments yet.</p>';
    return;
  }
  const listId = (format) => `decks-${format || "none"}`;
  const formats = new Set(data.tournaments.map((t) => t.format || ""));
  box.innerHTML = `${[...formats].map((f) => `<datalist id="${listId(f)}">${(data.suggestions[f] || [])
    .map((d) => `<option value="${esc(d)}"></option>`).join("")}</datalist>`).join("")}
    <div class="table-wrap"><table><thead><tr><th>Date</th><th>Tournament</th><th>Format</th><th>Deck</th></tr></thead><tbody>
    ${data.tournaments.map((t) => `<tr><td>${esc(t.date)}</td>
      <td><a href="tournaments.html?id=${encodeURIComponent(t.tournamentId)}">${esc(t.name)}</a></td>
      <td>${t.format ? esc(GAME_FORMATS[t.format] || t.format) : '<span class="muted">—</span>'}</td>
      <td><input type="text" class="deck-input" list="${listId(t.format)}" maxlength="60" value="${esc(t.deck || "")}"
        data-tid="${esc(t.tournamentId)}" placeholder="Choose or type a deck" aria-label="Deck for ${esc(t.name)}">
        <span class="deck-status muted" aria-live="polite"></span></td></tr>`).join("")}
    </tbody></table></div>`;

  box.querySelectorAll("input.deck-input").forEach((input) => input.addEventListener("change", async () => {
    const status = input.nextElementSibling;
    status.textContent = "Saving…";
    try {
      const r = await api(`/me/decks/${encodeURIComponent(input.dataset.tid)}`, { method: "POST", body: { deck: input.value } });
      input.value = r.deck || "";
      status.textContent = "Saved";
      // re-read suggestions so the new deck moves to the top of its format's list
      const fresh = await api("/me/decks");
      for (const f of formats) {
        box.querySelector(`#${listId(f)}`).innerHTML = (fresh.suggestions[f] || []).map((d) => `<option value="${esc(d)}"></option>`).join("");
      }
    } catch (err) {
      status.textContent = err.message;
    }
  }));
}

async function render() {
  if (!currentUser()) {
    content.innerHTML = CONFIG.auth.mode === "local"
      ? '<div class="msg">Local development mode: choose a dev user in the top-right menu.</div>'
      : `<p class="secondary">Sign in or create an account with your email. We'll send a verification code.</p>
         <button class="primary" id="login">Sign in / Sign up</button>`;
    document.getElementById("login")?.addEventListener("click", login);
    return;
  }
  const me = await api("/me");
  const player = me.player;
  let claimSection = "";
  if (me.claimStatus !== "approved") {
    const players = (await api("/leaderboard", { query: { ladder: "all" } })).players.filter((p) => !p.hidden);
    claimSection = `<h2>Claim your player profile</h2>
      <p class="secondary">Pick your name as it appears in tournament results. An admin will verify it's really you,
        usually by checking with you at the store or against the email you registered with the tournament organizer.</p>
      <form id="claim" class="card"><div class="form-grid">
        <label class="field">Player<select name="playerId">${players.map((p) => `<option value="${esc(p.playerId)}">${esc(p.displayName)}</option>`).join("")}</select></label>
        <label class="field">Note for the admin (optional)<input type="text" name="note" maxlength="500" placeholder="e.g. I play Thursdays at Store X, DCI 123…"></label>
      </div><button class="primary">Request claim</button><div id="claim-msg"></div></form>`;
  }
  content.innerHTML = `
    <div class="card">
      <p><strong>${esc(me.email)}</strong>${me.isAdmin ? ' <span class="badge type-rel">ADMIN</span>' : ""}${badge(me.membership)}</p>
      <p>${STATUS[me.claimStatus] || ""} ${player ? `Player: ${playerLink(player.playerId, player.displayName, "all")}` : ""}</p>
    </div>
    ${me.claimStatus === "approved" ? `<h2>Privacy</h2>
      <div class="card"><label class="row"><input type="checkbox" id="hidden" ${player?.hidden ? "checked" : ""}>
        Hide my name. It will show as “Hidden player” everywhere. Your rating is still counted.</label><div id="privacy-msg"></div></div>` : ""}
    ${me.claimStatus === "approved" ? `<h2 id="decks">My decks</h2>
      <p class="secondary">Pick or type the deck you played in each tournament. Your most recent decks come first.</p>
      <div id="decks-box"><p class="muted">Loading…</p></div>` : ""}
    ${claimSection}
    <h2>Membership</h2>
    <p class="secondary">Supporter and Diamond memberships are coming soon. Viewing and uploads will always stay free.</p>
    <h2>Delete account</h2>
    <div class="card">
      <p class="secondary">Deletes your email and login. Your player profile and results stay on the site, no longer linked to you.
        To have your name removed from tournament results as well, see the <a href="privacy.html">privacy notice</a>.</p>
      <button class="secondary" id="delete-account">Delete my account</button><div id="delete-msg"></div></div>`;

  if (me.claimStatus === "approved") renderDecks().catch((err) => showError(document.getElementById("decks-box"), err));

  document.getElementById("delete-account").addEventListener("click", async () => {
    if (!confirm("Delete your account? This cannot be undone.")) return;
    try {
      await api("/me", { method: "DELETE" });
      logout();
    } catch (err) { showError(document.getElementById("delete-msg"), err); }
  });

  document.getElementById("hidden")?.addEventListener("change", async (e) => {
    const msg = document.getElementById("privacy-msg");
    try {
      await api("/me/privacy", { method: "POST", body: { hidden: e.target.checked } });
      msg.innerHTML = `<div class="msg ok">${e.target.checked ? "Your name is now hidden." : "Your name is visible again."}</div>`;
    } catch (err) { showError(msg, err); }
  });
  document.getElementById("claim")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/me/claim", { method: "POST", body: Object.fromEntries(new FormData(e.target)) });
      render();
    } catch (err) { showError(document.getElementById("claim-msg"), err); }
  });
}

render().catch((err) => showError(content, err));
