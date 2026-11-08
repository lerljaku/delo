// Popup of the mtgdelo extension: reads the results of the Eventlink event in the current tab (as the
// signed-in Eventlink user; their session never leaves the browser) and uploads them to mtgdelo's
// anonymous POST /api/eventlink. The server keeps one tournament per Eventlink event, so uploading
// twice is harmless.
const DEFAULT_SITE = "https://mtgdelo.com";
// game formats, as in backend/delo/elo.py GAME_FORMATS
const FORMATS = { modern: "Modern", limited: "Limited", "duel-commander": "Duel Commander", edh: "EDH",
  legacy: "Legacy", vintage: "Vintage", premodern: "Premodern" };

const $ = (id) => document.getElementById(id);
const form = $("form");
const status = $("status");
const result = $("result");

function show(el, message, kind = "") {
  el.className = `msg ${kind}`;
  el.textContent = message;
  el.hidden = false;
}

async function siteUrl() {
  const { site } = await chrome.storage.sync.get({ site: DEFAULT_SITE });
  return site.replace(/\/+$/, "");
}

async function api(path, options = {}) {
  const site = await siteUrl();
  const resp = await fetch(`${site}/api${path}`, options);
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(body.error || `mtgdelo answered ${resp.status}`);
  return body;
}

/** An upload, run by mtgdelo in the background (rating can take longer than a request may): waits for it
 * and returns, or throws, what it answered. */
async function backgroundApi(path, options) {
  const job = await api(`${path}${path.includes("?") ? "&" : "?"}async=1`, options);
  for (;;) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    const state = await api(`/jobs/${job.jobId}`);
    if (state.status !== "done") continue;
    if (state.statusCode >= 400) throw new Error(state.result?.error || `mtgdelo answered ${state.statusCode}`);
    return state.result;
  }
}

/** Runs in the Eventlink tab: the event's ids and name, and every round's pairings as an Eventlink
 * copied-pairings paste. Injected by chrome.scripting, so it may use nothing from outside itself. */
async function readEvent() {
  const event = location.pathname.match(/(?:\/stores\/(\d+))?\/events\/(\d+)(?:\/|$)/);
  if (location.hostname !== "eventlink.wizards.com" || !event) return { error: "not-event" };
  const [, storeId = "", eventId] = event;
  const base = location.origin + location.pathname.slice(0, event.index + event[0].replace(/\/$/, "").length);
  const text = (el) => (el?.textContent || "").replace(/\s+/g, " ").trim(); // as copied: "David  Sýkora" -> one space

  // one round's pairings page: {text, matches, unfinished, players, label, round}, or null while it is loading.
  // label: the round the page says it shows ("Round 3", "Final"); round: the most matches a player
  // has (W–L–D records), a fallback for pages without a label. A page still showing another round
  // gives away that way. Records aren't always up to date: the Final's leave the final out.
  const scrape = (doc) => {
    const rows = [...doc.querySelectorAll(".pairings-table tbody tr")];
    if (!rows.length) return null;
    const lines = [];
    const players = [];
    let matches = 0;
    let unfinished = 0;
    let round = 0;
    for (const row of rows) {
      const teams = [...row.querySelectorAll(".team")]
        .map((t) => ({ name: text(t.querySelector(".team__display-name")), record: text(t.querySelector(".team__record")) }))
        .filter((t) => t.name);
      if (!teams.length) continue;
      for (const t of teams) round = Math.max(round, (t.record.match(/\d+/g) || []).reduce((a, b) => a + +b, 0));
      if (teams.length === 1 || /^bye$/i.test(teams[1].name)) { // a bye: no table, counted as a 2-0 win
        lines.push(teams[0].name, teams[0].record, "20", "Bye");
        players.push(teams[0].name);
        continue;
      }
      const scores = [...row.querySelectorAll(".box-score")].map(text);
      if (scores.length < 2 || !scores.every((s) => /^\d+$/.test(s))) {
        unfinished++;
        continue;
      }
      const table = text(row.querySelector(".pairings-table__cell--table-num"));
      if (/^\d+$/.test(table)) lines.push(table);
      lines.push(teams[0].name, teams[0].record, scores.join(""), teams[1].name, teams[1].record);
      players.push(teams[0].name, teams[1].name);
      matches++;
    }
    const label = text(doc.querySelector(".event-page-header__reminder-round"));
    return { text: lines.length ? `${lines.join("\n")}\n` : "", matches, unfinished, players, label, round };
  };
  // (records of a round without all results still show the round before)
  const showsRound = (page, n) => (page.label ? page.label.toLowerCase() === labels[n].toLowerCase()
    : page.round === n || page.unfinished > 0);

  // One hidden frame of this (signed-in) Eventlink tab reads every round. After the first, it moves
  // to the next round inside the running app, as a click in the sidebar would: starting the whole
  // app again for each round was slow and sometimes never finished.
  const frame = document.createElement("iframe");
  frame.style.cssText = "position:fixed;left:-10000px;top:0;width:1200px;height:900px;visibility:hidden";

  // {round} once the frame shows round n's pairings, else after `ms` {seen}: what it showed last
  const waitForRound = (n, ms) => new Promise((resolve) => {
    const started = Date.now();
    let seen = null;
    const poll = setInterval(() => {
      let round = null;
      try {
        const doc = frame.contentDocument;
        round = scrape(doc);
        seen = { url: frame.contentWindow.location.pathname, label: round?.label ?? text(doc.querySelector(".event-page-header__reminder-round")),
          rows: round ? round.matches + round.unfinished : 0, records: round?.round ?? 0,
          page: (doc.body?.innerText || "").replace(/\s+/g, " ").trim().slice(0, 80) }; // visible text only
      } catch { /* still loading */ }
      if (round && !showsRound(round, n)) round = null; // not this round's results (yet)
      if (round || Date.now() - started > ms) {
        clearInterval(poll);
        resolve({ round, seen });
      }
    }, 300);
  });

  // round n's pairings: switched to inside the app (10 s), else loaded afresh (30 s, twice)
  const loadRound = async (n) => {
    const url = `${base}/rounds/${n}/pairings`;
    let result = { round: null, seen: null };
    for (const how of ["in-app", "load", "load"]) {
      try {
        if (how === "in-app") {
          if (!frame.contentWindow.location.pathname.includes("/rounds/")) continue; // nothing loaded yet
          // the app's router follows the address on popstate, as on the browser's Back button
          frame.contentWindow.history.pushState(null, "", url);
          frame.contentWindow.dispatchEvent(new PopStateEvent("popstate"));
        } else {
          frame.contentWindow.location.replace(url);
        }
      } catch {
        continue;
      }
      result = await waitForRound(n, how === "in-app" ? 10000 : 30000);
      if (result.round) return result;
    }
    return result;
  };

  // the sidebar links every round that has started: Swiss rounds …/events/<id>/rounds/<n>/standings,
  // playoff ones (Quarterfinal, Semifinal, Final) just …/rounds/<n>
  const ROUND_LABEL = /^(Round \d+|Top \d+|Quarterfinals?|Semifinals?|Finals?)$/i;
  const labels = {}; // round number -> its sidebar name, for messages
  for (const a of document.querySelectorAll("a[href]")) {
    const m = a.href.match(/\/events\/(\d+)\/rounds\/(\d+)(?:[/?#]|$)/);
    const nav = text(a.querySelector(".our-nav-item__label")); // other links to a round (buttons, footer) have none
    if (m && m[1] === eventId && (nav || !labels[m[2]])) labels[m[2]] = nav || `Round ${m[2]}`;
  }
  const rounds = Object.keys(labels).map(Number).sort((a, b) => a - b);
  if (!rounds.length) return { error: "no-rounds" };
  const navRounds = [...document.querySelectorAll(".our-nav-item")]
    .filter((el) => ROUND_LABEL.test(text(el.querySelector(".our-nav-item__label"))));
  const notStarted = navRounds.filter((el) => el.classList.contains("our-nav-item--disabled"))
    .map((el) => text(el.querySelector(".our-nav-item__label")));
  // never upload with a round left out: the sidebar shows more started rounds than links were found
  if (navRounds.length - notStarted.length > rounds.length) {
    return { error: "missing-rounds", listed: navRounds.length - notStarted.length, found: rounds.length };
  }
  // the page heading cuts long names ("32nd PDC - Černý Rytíř CD…"); the tab title has them whole:
  // "EventLink - Round 8 of 32nd PDC - Černý Rytíř CDC League - Timer Completed"
  const heading = text(document.querySelector(".event-page-header__title"));
  const titled = document.title.replace(/^EventLink\s*-\s*/, "")
    .replace(/^(Round \d+|Top \d+|Quarterfinals?|Semifinals?|Finals?) of\s+/i, "").replace(/\s+-\s+Timer Completed$/i, "");
  const name = heading.endsWith("…") && titled.startsWith(heading.slice(0, -1).trim()) ? titled : heading || titled;
  const out = { storeId, eventId, rounds: [], notStarted, players: 0, matches: 0, content: "", name };
  const players = new Set();
  document.body.append(frame);
  try {
    for (const n of rounds) {
      const { round, seen } = await loadRound(n);
      if (!round) return { ...out, error: "unreadable", round: labels[n], seen };
      if (round.unfinished) return { ...out, error: "unfinished", round: labels[n], unfinished: round.unfinished };
      out.rounds.push(n);
      out.content += round.text;
      out.matches += round.matches;
      round.players.forEach((p) => players.add(p));
    }
  } finally {
    frame.remove();
  }
  out.players = players.size;
  return out;
}

const ERRORS = {
  "not-event": () => "Open a tournament in Eventlink (eventlink.wizards.com), then click the mtgdelo button again.",
  "no-rounds": () => "This event has no rounds yet.",
  "missing-rounds": (e) => `Eventlink lists ${e.listed} rounds but only ${e.found} could be found. Nothing was uploaded: `
    + "please report this, with the event's link.",
  unreadable: (e) => `Couldn't read the pairings of ${e.round}. Check that Eventlink shows them, then try again. `
    + (e.seen ? `(The page showed: ${e.seen.url}, round "${e.seen.label || "none"}", ${e.seen.rows} match rows, `
      + `records up to ${e.seen.records} matches, text: "${e.seen.page}")` : "(The page didn't load.)"),
  unfinished: (e) => `${e.round} has ${e.unfinished} match(es) without a result. Upload once it is finished.`,
};

async function main() {
  $("version").textContent = `v${chrome.runtime.getManifest().version}`;
  const site = await siteUrl();
  $("site").value = site;
  $("site").addEventListener("change", async () => {
    const value = $("site").value.trim().replace(/\/+$/, "") || DEFAULT_SITE;
    const allowed = await chrome.permissions.contains({ origins: [`${value}/*`] }).catch(() => false);
    if (!allowed) return show(result, `The extension may only talk to ${DEFAULT_SITE} or localhost.`, "error");
    await chrome.storage.sync.set({ site: value });
    location.reload();
  });

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!/^https:\/\/eventlink\.wizards\.com\//.test(tab?.url || "")) return show(status, ERRORS["not-event"](), "error");
  show(status, "Reading the event's rounds… keep this popup open.");
  let event;
  try {
    [{ result: event }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: readEvent });
  } catch (err) {
    return show(status, `Couldn't read the page: ${err.message}`, "error");
  }
  if (event.error) return show(status, ERRORS[event.error](event), "error");

  show(status, `${event.players} players, ${event.matches} matches in ${event.rounds.length} round(s).`
    + (event.notStarted.length ? ` Not started yet: ${event.notStarted.join(", ")}. Upload again after them to update.` : ""));
  form.format.innerHTML += Object.entries(FORMATS).map(([id, label]) => `<option value="${id}">${label}</option>`).join("");
  form.name.value = event.name;
  form.format.value = Object.keys(FORMATS).find((id) => event.name.toLowerCase().includes(FORMATS[id].toLowerCase())) || "";
  form.date.valueAsDate = new Date(); // Eventlink pages don't show the date
  const saved = await chrome.storage.local.get(`event:${event.eventId}`); // details picked at an earlier upload
  Object.entries(saved[`event:${event.eventId}`] || {}).forEach(([k, v]) => { if (form[k] && v) form[k].value = v; });
  form.hidden = false;

  // planned tournaments of the last 2 weeks (the site's upcoming tournaments): picking one fills in its details
  let planned = [];
  const day = (iso) => new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  const loadPlanned = async () => {
    const island = form.island.value ? `&island=${encodeURIComponent(form.island.value)}` : "";
    planned = await api(`/planned-tournaments?days=14${island}`).catch(() => []);
    form.planned.replaceChildren(new Option("Not in the list", ""), ...planned.map((e, i) => new Option(
      `${day(e.date)} · ${e.name}${e.repeats ? " ↻" : ""}${e.location ? ` (${e.location})` : ""}`, String(i))));
    $("planned-field").hidden = !planned.length;
  };
  form.planned.addEventListener("change", () => {
    const e = planned[form.planned.value];
    if (!e) return;
    form.name.value = e.name;
    form.date.value = e.date;
    if (FORMATS[e.format]) form.format.value = e.format;
    if (e.type) form.type.value = e.type;
  });
  form.island.addEventListener("change", loadPlanned); // each island has its own tournaments

  api("/islands").then((islands) => {
    if (!islands.length) return;
    for (const i of islands) form.island.append(new Option(i.name, i.islandId));
    if (saved[`event:${event.eventId}`]?.island) form.island.value = saved[`event:${event.eventId}`].island;
    $("island-field").hidden = false;
  }).catch(() => {}) // the server picks its default island
    .finally(loadPlanned);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const upload = $("upload");
    if (upload.disabled) return;
    upload.disabled = true; // one click, one request
    show(result, "Uploading and rating… keep this popup open.");
    const details = { name: form.name.value.trim(), date: form.date.value, format: form.format.value,
      type: form.type.value, island: form.island.value };
    try {
      const query = details.island ? `?island=${encodeURIComponent(details.island)}` : "";
      const r = await backgroundApi(`/eventlink${query}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...details, content: event.content, eventId: event.eventId, storeId: event.storeId }),
      });
      await chrome.storage.local.set({ [`event:${event.eventId}`]: details });
      const what = r.unchanged ? "Already on mtgdelo with these results: nothing changed."
        : r.updated ? "Updated: the earlier upload of this event was replaced and ratings recalculated."
          : "Uploaded and rated.";
      show(result, `${what} `, "ok");
      const link = document.createElement("a");
      link.href = `${await siteUrl()}/tournaments.html?id=${encodeURIComponent(r.tournamentId)}`;
      link.target = "_blank";
      link.textContent = "View tournament";
      result.append(link);
      upload.textContent = "Uploaded";
    } catch (err) {
      show(result, err.message, "error");
      upload.disabled = false;
    }
  });
}

main();
