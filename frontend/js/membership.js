import { api, badge, CONFIG, currentUser, esc, login, MEMBERSHIP_ENABLED, renderNav, showError } from "./common.js";

if (!MEMBERSHIP_ENABLED) location.replace("index.html"); // hidden for now (show_membership)
renderNav("membership.html");
const content = document.getElementById("content");
const pay = CONFIG.payments || {};

// Keep in sync with docs/ARCHITECTURE.md §7 and the perks enforced in backend/delo/premium.py.
const PLANS = [
  { id: "", name: "Free", price: "$0", perks: ["Rating, stats and Elo chart", "Claim your player profile", "Hide your name", "Record your decks",
    "Profile flair in your colour"] },
  { id: "supporter", name: "Supporter", price: "$5 once", link: pay.supporterLink,
    perks: ["Supporter badge next to your name", "Early access to new features"] },
  { id: "diamond", name: "Diamond", price: "$5 / month", link: pay.diamondLink,
    perks: ["Diamond badge", "Private notes on your matches",
      "Your matchups by opponent's deck", "Export your history as CSV", "Custom profile URL", "Post upcoming tournaments"] },
];
const RANK = { "": 0, supporter: 1, diamond: 2 };

// Stripe Payment Link with the Cognito sub, so the webhook knows whose membership it is.
function checkoutUrl(link, user) {
  const url = new URL(link);
  url.searchParams.set("client_reference_id", user.sub);
  if (user.email) url.searchParams.set("prefilled_email", user.email);
  return url.toString();
}

async function render() {
  const user = currentUser();
  const me = user ? await api("/me") : null;
  const level = me?.effectiveMembership || "";
  content.innerHTML = `
    ${me ? `<div class="msg">Your membership: <strong>${level ? `${badge(level)}` : "Free"}</strong>
      ${me.isAdmin ? " (admins have every feature)" : ""}
      ${me.diamond && pay.portalLink ? ` · <a href="${esc(pay.portalLink)}" rel="noopener">Manage or cancel your subscription</a>` : ""}
      ${me.claimStatus !== "approved" ? ' · Perks apply to your player profile once your <a href="account.html">claim</a> is approved.' : ""}</div>` : ""}
    <div class="plans">${PLANS.map((p) => `<div class="card plan ${p.id ? `plan-${p.id}` : ""}">
      <h2>${p.name}</h2><div class="price">${p.price}</div>
      <ul>${p.perks.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
      ${action(p, user, level)}</div>`).join("")}</div>
    <p class="muted">Payments are handled by Stripe; we never see your card. Diamond renews monthly and can be cancelled at any time,
      and you keep it until the end of the paid month. Notes you wrote stay readable after it ends.</p>`;
  content.querySelectorAll("button.signin").forEach((b) => b.addEventListener("click", login));
}

function action(plan, user, level) {
  if (!plan.id) return "";
  if (RANK[level] >= RANK[plan.id]) return '<p class="muted">✓ You have this</p>';
  if (!plan.link) return '<p class="muted">Coming soon</p>';
  if (!user) return '<button class="primary signin">Sign in to join</button>';
  return `<a class="button primary" href="${esc(checkoutUrl(plan.link, user))}" rel="noopener">Become ${plan.name}</a>`;
}

if (MEMBERSHIP_ENABLED) render().catch((err) => showError(content, err));
