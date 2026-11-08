// "Upcoming tournaments" sidebar on the leaderboard page. Everyone sees the
// list (soonest first); Diamond members and admins can post, and remove what they posted
// (admins: anything). Recurring tournaments (weekly/monthly) show only their next date, with a
// note when that day is a public holiday.
import { api, currentUser, esc, externalLink, formatBadge, GAME_FORMATS, MEMBERSHIP_ENABLED, showError, typeBadge } from "./common.js";

const fmtDate = (iso) => new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });

function eventItem(e, removable) {
  return `<li class="event">
    <div class="event-date">${esc(fmtDate(e.date))}${e.time ? ` · ${esc(e.time)}` : ""}</div>
    <div class="event-name">${e.link ? externalLink(e.link, e.name) : esc(e.name)}</div>
    ${e.type || e.format ? `<div class="event-meta">${e.type ? typeBadge(e.type) : ""}${formatBadge(e.format)}</div>` : ""}
    ${e.location ? `<div class="event-location" title="Place">📍 ${esc(e.location)}</div>` : ""}
    ${e.repeats ? `<div class="event-repeats" title="Recurring tournament: only the next date is shown">↻ ${esc(e.repeats)}</div>` : ""}
    ${e.holiday ? `<div class="event-holiday">⚠ Public holiday (${esc(e.holiday)}): this one might be cancelled</div>` : ""}
    ${removable ? `<button class="link-button" data-remove="${esc(e.eventId)}">Remove</button>` : ""}</li>`;
}

function postForm() {
  const today = new Date().toISOString().slice(0, 10);
  return `<details class="post-event"><summary>Post a tournament</summary>
    <form id="event-form">
      <label class="field">Name<input type="text" name="name" required maxlength="100" placeholder="Modern Monday"></label>
      <label class="field">Date <span class="muted small">(first one, if it repeats)</span><input type="date" name="date" required min="${today}"></label>
      <label class="field">Repeats<select name="recurrence"><option value="">Doesn't repeat</option>
        <option value="weekly">Every week</option><option value="monthly">Every month</option></select></label>
      <label class="field">Time<input type="text" name="time" maxlength="20" placeholder="18:00"></label>
      <label class="field">Place<input type="text" name="location" maxlength="100" placeholder="Store, city"></label>
      <label class="field">Format<select name="format"><option value="">Any / not decided</option>
        ${Object.entries(GAME_FORMATS).map(([id, label]) => `<option value="${id}">${label}</option>`).join("")}</select></label>
      <label class="field">Type<select name="type"><option value="">—</option><option value="rel">REL</option><option value="casual">Casual</option></select></label>
      <label class="field">Link<input type="url" name="link" placeholder="https://… sign-up or event page"></label>
      <button class="primary">Post</button><div id="event-msg"></div></form></details>`;
}

export async function renderUpcoming(aside) {
  const user = currentUser();
  const [events, mine] = await Promise.all([
    api("/upcoming"),
    user ? api("/me/upcoming").catch(() => null) : null,
  ]);
  const own = new Set(mine?.eventIds || []);
  aside.innerHTML = `<h2>Upcoming tournaments</h2>
    ${events.length ? `<ul class="events">${events.map((e) => eventItem(e, user?.isAdmin || own.has(e.eventId))).join("")}</ul>`
      : '<p class="muted">Nothing announced yet.</p>'}
    ${mine?.canPost ? postForm() : MEMBERSHIP_ENABLED ? '<p class="muted small">Diamond members can post tournaments here. <a href="membership.html">Membership</a></p>' : ""}`;

  aside.querySelectorAll("button[data-remove]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Remove this tournament from the list?")) return;
    try {
      await api(`/upcoming/${encodeURIComponent(b.dataset.remove)}`, { method: "DELETE" });
      renderUpcoming(aside);
    } catch (err) { alert(err.message); }
  }));
  const form = aside.querySelector("#event-form");
  form?.date.addEventListener("change", () => {
    const d = form.date.value && new Date(`${form.date.value}T00:00:00`);
    const [weekly, monthly] = form.recurrence.querySelectorAll("option[value]:not([value=''])");
    weekly.textContent = d ? `Every week on ${d.toLocaleDateString(undefined, { weekday: "long" })}` : "Every week";
    monthly.textContent = d ? `Every month on day ${d.getDate()}` : "Every month";
  });
  aside.querySelector("#event-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/upcoming", { method: "POST", body: Object.fromEntries(new FormData(e.target)) });
      renderUpcoming(aside);
    } catch (err) { showError(aside.querySelector("#event-msg"), err); }
  });
}

export function mountUpcoming(id = "upcoming") {
  const aside = document.getElementById(id);
  if (aside) renderUpcoming(aside).catch((err) => showError(aside, err));
}
