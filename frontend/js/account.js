import {
  api, badge, CONFIG, currentUser, esc, FLAIR_COLORS, GAME_FORMATS, login, logout, MEMBERSHIP_ENABLED, playerLink, renderNav,
  showError,
} from "./common.js";
import { bindDeckInputs, deckDatalists, deckInput } from "./decks.js";

renderNav("account.html");
const content = document.getElementById("content");

const STATUS = {
  none: "You haven't claimed a player profile yet.",
  pending: "Your claim is waiting for an admin to verify it.",
  approved: "Verified. This player profile is yours.",
  rejected: "Your last claim was rejected. Contact an admin if this is a mistake.",
};

async function renderDecks() {
  const box = document.getElementById("decks-box");
  const data = await api("/me/decks");
  if (!data.tournaments.length) {
    box.innerHTML = '<p class="muted">No tournaments yet.</p>';
    return;
  }
  box.innerHTML = `${deckDatalists(data)}
    <div class="table-wrap"><table><thead><tr><th>Date</th><th>Tournament</th><th>Format</th><th>Deck</th></tr></thead><tbody>
    ${data.tournaments.map((t) => `<tr><td>${esc(t.date)}</td>
      <td><a href="tournaments.html?id=${encodeURIComponent(t.tournamentId)}">${esc(t.name)}</a></td>
      <td>${t.format ? esc(GAME_FORMATS[t.format] || t.format) : '<span class="muted">—</span>'}</td>
      <td>${deckInput(t, t.deck)}</td></tr>`).join("")}
    </tbody></table></div>`;
  bindDeckInputs(box);
}

// Flair for everyone with a claimed profile; the custom profile URL is a Diamond perk (shown
// disabled to others only while the membership page is on).
function profileSection(me) {
  const p = me.player || {};
  const diamond = me.effectiveMembership === "diamond";
  const url = `${location.origin}/player.html?u=`;
  return `<h2>Profile</h2>
    <form id="profile-form" class="card">
      <p class="secondary">A short flair is shown next to your name on the leaderboard and your player page.</p>
      <div class="form-grid">
      <label class="field">Flair<input type="text" name="flair" maxlength="30" value="${esc(p.flair || "")}" placeholder="e.g. Kroxa enjoyer"></label>
      <label class="field">Flair colour<select name="flairColor">
        ${FLAIR_COLORS.map((c) => `<option value="${c}" ${c === (p.flairColor || "gray") ? "selected" : ""}>${c}</option>`).join("")}</select></label>
      ${diamond || MEMBERSHIP_ENABLED ? `<label class="field">Profile URL ${diamond ? "" : '<span class="muted">(Diamond)</span>'}
        <input type="text" name="slug" maxlength="30" value="${esc(p.slug || "")}" placeholder="your-name" ${diamond ? "" : "disabled"}
          title="${esc(url)}your-name"></label>` : ""}
    </div>
    ${p.slug ? `<p class="secondary">Your profile: <a href="player.html?u=${encodeURIComponent(p.slug)}">${esc(url + p.slug)}</a></p>` : ""}
    <button class="primary">Save profile</button><div id="profile-msg"></div></form>`;
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
    ${claimSection}
    ${MEMBERSHIP_ENABLED ? `<h2>Membership</h2>
    <p>${me.effectiveMembership ? `You are a ${badge(me.effectiveMembership)} member.` : "Free."}
      <a href="membership.html">${me.effectiveMembership === "diamond" ? "Membership details" : "See what Supporter and Diamond add"}</a></p>`
    : me.effectiveMembership ? `<h2>Membership</h2><p>You are a ${badge(me.effectiveMembership)} member.</p>` : ""}
    ${me.claimStatus === "approved" ? profileSection(me) : ""}
    ${me.claimStatus === "approved" ? `<h2>Privacy</h2>
      <div class="card"><label class="row"><input type="checkbox" id="hidden" ${player?.hidden ? "checked" : ""}>
        Hide my name. It will show as “Hidden player” everywhere. Your rating is still counted.</label><div id="privacy-msg"></div></div>` : ""}
    ${me.claimStatus === "approved" ? `<h2 id="decks">My decks</h2>
      <p class="secondary">Pick or type the deck you played in each tournament. Your most recent decks come first.</p>
      <div id="decks-box"><p class="muted">Loading…</p></div>` : ""}
    <h2>Delete account</h2>
    <div class="card">
      <p class="secondary">Deletes your email and login. Your player profile and results stay on the site, no longer linked to you.
        To have your name removed from tournament results as well, see the <a href="privacy.html">privacy notice</a>.</p>
      <button class="secondary" id="delete-account">Delete my account</button><div id="delete-msg"></div></div>`;

  if (me.claimStatus === "approved") renderDecks().catch((err) => showError(document.getElementById("decks-box"), err));

  document.getElementById("profile-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = document.getElementById("profile-msg");
    const body = Object.fromEntries(new FormData(e.target));
    try {
      await api("/me/profile", { method: "POST", body });
      msg.innerHTML = '<div class="msg ok">Saved.</div>';
    } catch (err) { showError(msg, err); }
  });

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
