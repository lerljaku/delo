import { api, badge, CONFIG, currentUser, esc, login, playerLink, renderNav, showError } from "./common.js";

renderNav("account.html");
const content = document.getElementById("content");

const STATUS = {
  none: "You haven't claimed a player profile yet.",
  pending: "Your claim is waiting for an admin to verify it.",
  approved: "Verified. This player profile is yours.",
  rejected: "Your last claim was rejected. Contact an admin if this is a mistake.",
};

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
    ${claimSection}
    <h2>Membership</h2>
    <p class="secondary">Supporter and Diamond memberships are coming soon. Viewing and uploads will always stay free.</p>`;

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
