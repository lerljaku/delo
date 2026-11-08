// Shared helpers: config, auth (Cognito Hosted UI with PKCE, or fake local users), API, nav.
export const CONFIG = window.DELO_CONFIG || { apiBase: "/api", auth: { mode: "local" } };
// Membership page and its links/upsells (Terraform variable show_membership). Off: memberships
// granted by an admin still work, they just aren't advertised or sold.
export const MEMBERSHIP_ENABLED = CONFIG.features?.membership === true;

const TOKEN_KEY = "delo.idToken";
const DEV_USER_KEY = "delo.devUser";
const LADDER_KEY = "delo.ladder";
const FORMAT_KEY = "delo.format";

export const HIDDEN_NAME = "Hidden player";

// ---------------------------------------------------------------- islands
// The selected island (a separately rated group of players, see backend/delo/elo.py) lives in a
// cookie so it is preselected on every visit; api() sends it with every request. No cookie or an
// unknown island: the server uses the default island.
const ISLAND_COOKIE = "delo_island";
export function getIsland() {
  const m = document.cookie.match(/(?:^|;\s*)delo_island=([^;]*)/);
  return m ? decodeURIComponent(m[1]) : "";
}
export function setIsland(island) {
  document.cookie = `${ISLAND_COOKIE}=${encodeURIComponent(island)}; path=/; max-age=31536000; SameSite=Lax`;
}
// Same ids as GAME_FORMATS in backend/delo/elo.py. "" = all formats (global leaderboard).
export const GAME_FORMATS = {
  modern: "Modern", limited: "Limited", "duel-commander": "Duel Commander", edh: "EDH",
  legacy: "Legacy", vintage: "Vintage", premodern: "Premodern",
};

// ---------------------------------------------------------------- formatting
export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
export const fmtRating = (r) => Math.round(r).toString();
export const fmtPct = (x) => `${(x * 100).toFixed(1)}%`;
export function fmtDelta(d) {
  const r = Math.round(d * 10) / 10;
  const cls = r > 0 ? "delta-pos" : r < 0 ? "delta-neg" : "muted";
  const sign = r > 0 ? "+" : r < 0 ? "−" : "±";
  return `<span class="${cls}">${sign}${Math.abs(r).toFixed(1)}</span>`;
}
export function badge(membership) {
  if (membership === "diamond") return '<span class="badge diamond" title="Diamond member">◆ Diamond</span>';
  // donors (support.html), marked by an admin: a diamond emoji with a tooltip
  if (membership === "supporter") return '<span class="supporter-mark" title="Supporter of this project" aria-label="Supporter">💎</span>';
  return "";
}
// Supporter perk: a short custom label next to the name (colours: FLAIR_COLORS in backend/delo/premium.py).
export const FLAIR_COLORS = ["blue", "green", "purple", "orange", "red", "gray"];
export function flairBadge(flair, color) {
  return flair ? `<span class="badge flair flair-${FLAIR_COLORS.includes(color) ? color : "gray"}">${esc(flair)}</span>` : "";
}
export function typeBadge(type) {
  return `<span class="badge type-${esc(type)}">${type === "rel" ? "REL" : "Casual"}</span>`;
}
export function formatBadge(format) {
  return format ? `<span class="badge format">${esc(GAME_FORMATS[format] || format)}</span>` : "";
}
export function playerLink(id, name, ladder, format) {
  if (!id) return '<span class="muted">BYE</span>';
  const cls = name === HIDDEN_NAME ? ' class="hidden-name"' : "";
  // format is always in the URL: without it the player page falls back to the saved format
  const q = new URLSearchParams({ id, ladder: ladder || getLadder(), format: format ?? getFormat() });
  return `<a${cls} href="player.html?${q}">${esc(name)}</a>`;
}
// Opens in a new tab; hrefs come from the API, which only accepts http(s) links.
export const externalLink = (url, label) =>
  /^https?:\/\//.test(url || "") ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer nofollow">${esc(label || url)}</a>` : "";
export const resultCell = (r) => `<span class="res-${esc(r)}">${esc(r)}</span>`;
export const param = (name) => new URLSearchParams(location.search).get(name);

export function getLadder() {
  const fromUrl = param("ladder");
  if (fromUrl === "rel" || fromUrl === "all") return fromUrl;
  return localStorage.getItem(LADDER_KEY) || "rel";
}
export function setLadder(ladder) {
  localStorage.setItem(LADDER_KEY, ladder);
  const url = new URL(location.href);
  url.searchParams.set("ladder", ladder);
  history.replaceState(null, "", url);
}

export function getFormat() {
  const fromUrl = param("format");
  if (fromUrl !== null) return Object.hasOwn(GAME_FORMATS, fromUrl) ? fromUrl : "";
  const saved = localStorage.getItem(FORMAT_KEY) || "";
  return Object.hasOwn(GAME_FORMATS, saved) ? saved : "";
}
export function setFormat(format) {
  localStorage.setItem(FORMAT_KEY, format);
  const url = new URL(location.href);
  url.searchParams.set("format", format);
  history.replaceState(null, "", url);
}
export const ladderLabel = (ladder, format) =>
  `${format ? `${GAME_FORMATS[format]} · ` : ""}${ladder === "rel" ? "REL" : "REL + Casual"}`;

// All formats / Modern / ... select. Calls onChange(format).
export function formatSelect(container, onChange) {
  const current = getFormat();
  container.innerHTML = `<select aria-label="Format"><option value="">All formats</option>${Object.entries(GAME_FORMATS)
    .map(([id, label]) => `<option value="${id}" ${id === current ? "selected" : ""}>${label}</option>`).join("")}</select>`;
  container.querySelector("select").addEventListener("change", (e) => {
    setFormat(e.target.value);
    onChange(e.target.value);
  });
}

// Segmented REL / REL + Casual control. Calls onChange(ladder).
export function ladderToggle(container, onChange) {
  const current = getLadder();
  container.innerHTML = `<div class="segmented" role="group" aria-label="Ladder">
      <button data-l="rel" aria-pressed="${current === "rel"}">REL</button>
      <button data-l="all" aria-pressed="${current === "all"}">REL + Casual</button></div>`;
  container.querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => {
      container.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === b));
      setLadder(b.dataset.l);
      onChange(b.dataset.l);
    }),
  );
}

// ---------------------------------------------------------------- markdown (tiny, safe subset)
export function markdown(src) {
  const inline = (s) =>
    esc(s)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/`(.+?)`/g, "<code>$1</code>")
      .replace(/\[(.+?)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" rel="noopener">$1</a>');
  const out = [];
  let list = false;
  for (const line of src.split(/\r?\n/)) {
    const li = line.match(/^\s*[-*]\s+(.*)/);
    if (!li && list) { out.push("</ul>"); list = false; }
    const h = line.match(/^(#{1,4})\s+(.*)/);
    if (h) out.push(`<h${h[1].length + 1}>${inline(h[2])}</h${h[1].length + 1}>`);
    else if (li) { if (!list) { out.push("<ul>"); list = true; } out.push(`<li>${inline(li[1])}</li>`); }
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push("</ul>");
  return out.join("\n");
}

// ---------------------------------------------------------------- auth
function decodeJwt(token) {
  try {
    const part = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(decodeURIComponent(escape(atob(part))));
  } catch {
    return null;
  }
}

// Returns {email, isAdmin} or null.
export function currentUser() {
  if (CONFIG.auth.mode === "local") {
    const dev = localStorage.getItem(DEV_USER_KEY);
    if (!dev) return null;
    if (dev === "admin") return { sub: "dev-admin", email: "admin@localhost", isAdmin: true };
    // same sub as backend/local_server.py dev_claims
    return { sub: `dev-${dev.slice(5).replace(/[^a-z0-9]/gi, "-")}`, email: dev.slice(5), isAdmin: false };
  }
  const token = localStorage.getItem(TOKEN_KEY);
  const claims = token && decodeJwt(token);
  if (!claims || claims.exp * 1000 < Date.now()) {
    localStorage.removeItem(TOKEN_KEY);
    return null;
  }
  return { sub: claims.sub, email: claims.email, isAdmin: (claims["cognito:groups"] || []).includes("admin") };
}

const b64url = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

export async function login() {
  const a = CONFIG.auth;
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = b64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
  const state = b64url(crypto.getRandomValues(new Uint8Array(16)));
  sessionStorage.setItem("delo.pkce", JSON.stringify({ verifier, state, returnTo: location.pathname + location.search }));
  const q = new URLSearchParams({
    response_type: "code", client_id: a.clientId, redirect_uri: a.redirectUri, scope: "openid email profile",
    code_challenge_method: "S256", code_challenge: challenge, state,
  });
  location.href = `${a.domain}/oauth2/authorize?${q}`;
}

export async function completeLogin() {
  const a = CONFIG.auth;
  const saved = JSON.parse(sessionStorage.getItem("delo.pkce") || "{}");
  sessionStorage.removeItem("delo.pkce");
  if (!param("code") || param("state") !== saved.state) throw new Error("Login failed: invalid state");
  const resp = await fetch(`${a.domain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code", client_id: a.clientId, code: param("code"),
      redirect_uri: a.redirectUri, code_verifier: saved.verifier,
    }),
  });
  if (!resp.ok) throw new Error(`Login failed (${resp.status})`);
  localStorage.setItem(TOKEN_KEY, (await resp.json()).id_token);
  return saved.returnTo || "index.html";
}

export function logout() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(DEV_USER_KEY);
  if (CONFIG.auth.mode === "cognito") {
    const q = new URLSearchParams({ client_id: CONFIG.auth.clientId, logout_uri: CONFIG.auth.logoutUri });
    location.href = `${CONFIG.auth.domain}/logout?${q}`;
  } else {
    location.reload();
  }
}

// ---------------------------------------------------------------- API
// background: for requests that can recalculate every rating, longer than the API may take (30 s).
// The server runs them as a job; this waits for it and returns (or throws) what the request answered.
export async function api(path, { method = "GET", body, query, background = false } = {}) {
  if (background) {
    const job = await api(path, { method, body, query: { ...(query || {}), async: "1" } });
    if (!job?.jobId) return job; // answered right away (e.g. a preview)
    for (;;) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      const state = await api(`/jobs/${job.jobId}`);
      if (state.status !== "done") continue;
      if (state.statusCode < 400) return state.result;
      const err = new Error(state.result?.error || `Request failed (${state.statusCode})`);
      err.status = state.statusCode;
      err.data = state.result;
      throw err;
    }
  }
  const headers = {};
  if (CONFIG.auth.mode === "local") {
    const dev = localStorage.getItem(DEV_USER_KEY);
    if (dev) headers["X-Dev-User"] = dev;
  } else {
    const token = currentUser() && localStorage.getItem(TOKEN_KEY);
    if (token) headers.Authorization = `Bearer ${token}`;
  }
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const island = getIsland();
  const params = { ...(island ? { island } : {}), ...(query || {}) };
  const qs = Object.keys(params).length ? `?${new URLSearchParams(params)}` : "";
  const resp = await fetch(`${CONFIG.apiBase}${path}${qs}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  let data = null;
  try { data = await resp.json(); } catch { /* empty body */ }
  if (!resp.ok) {
    const err = new Error((data && (data.error || data.message)) || `Request failed (${resp.status})`);
    err.status = resp.status;
    err.data = data;
    throw err;
  }
  return data;
}

export function showError(el, err) {
  el.innerHTML = `<div class="msg error">${esc(err.message || err)}</div>`;
}

// ---------------------------------------------------------------- nav
export function renderNav(active) {
  const user = currentUser();
  const links = [
    ["index.html", "Leaderboard"],
    ["tournaments.html", "Tournaments"],
    ["upload.html", "Upload"],
    ["account.html", user ? "Account" : "Sign in"],
    ...(user?.isAdmin ? [["admin.html", "Admin"]] : []),
    ...(MEMBERSHIP_ENABLED ? [["membership.html", "Membership"]] : []),
    ["docs.html", "Docs"],
  ];

  let authBits = "";
  if (CONFIG.auth.mode === "local") {
    const dev = localStorage.getItem(DEV_USER_KEY) || "";
    const opts = [["", "Dev: signed out"], ["admin", "Dev: admin"], ["user:alice@example.com", "Dev: alice@example.com"], ["user:bob@example.com", "Dev: bob@example.com"]];
    authBits = `<select id="dev-user" title="Local development: pretend to be this user">${opts
      .map(([v, l]) => `<option value="${v}" ${v === dev ? "selected" : ""}>${l}</option>`).join("")}</select>`;
  } else if (user) {
    authBits = `<span class="muted">${esc(user.email)}</span> <a href="#" id="logout">Sign out</a>`;
  }

  const nav = document.createElement("nav");
  nav.className = "topnav";
  nav.innerHTML = `<div class="inner"><a class="brand" href="index.html" title="Delo">D<span>elo</span></a>
    <select id="island-select" class="island-select" aria-label="Island" title="Island: players who play each other" hidden></select>
    ${links.map(([href, label]) => `<a href="${href}" class="${href === active ? "active" : ""}">${label}${
      href === "admin.html" ? '<span class="nav-badge" id="claims-badge" hidden></span>' : ""}</a>`).join("")}
    <span class="spacer"></span>${authBits}</div>`;
  document.body.prepend(nav);
  islandPicker(nav.querySelector("#island-select"));
  if (user?.isAdmin) pendingClaimsBadge(nav.querySelector("#claims-badge"));

  nav.querySelector("#dev-user")?.addEventListener("change", (e) => {
    if (e.target.value) localStorage.setItem(DEV_USER_KEY, e.target.value);
    else localStorage.removeItem(DEV_USER_KEY);
    location.reload();
  });
  nav.querySelector("#logout")?.addEventListener("click", (e) => { e.preventDefault(); logout(); });

  const footer = document.createElement("footer");
  footer.innerHTML = `Delo · unofficial fan project, not affiliated with Wizards of the Coast ·
    <a href="support.html">Support this project</a> · <a href="privacy.html">Privacy</a> ·
    <a href="${esc(CONFIG.githubRepo || "#")}/issues/new/choose" rel="noopener">Report an issue</a>`;
  document.body.append(footer);
  countVisit(user);
}

// Island picker in the navigation; hidden while no island exists. Changing it reloads the page,
// so everything on it is shown for the new island.
async function islandPicker(select) {
  try {
    const islands = await api("/islands");
    if (!islands.length) return;
    const current = islands.some((i) => i.islandId === getIsland()) ? getIsland() : islands[0].islandId;
    select.innerHTML = islands.map((i) => `<option value="${esc(i.islandId)}" ${i.islandId === current ? "selected" : ""}>${esc(i.icon || "🏝")} ${esc(i.name)}</option>`).join("");
    select.hidden = false;
    select.addEventListener("change", () => { setIsland(select.value); location.reload(); });
  } catch { /* the site works without the picker (default island) */ }
}

// Admins: number of player claims waiting for review, on the Admin link.
async function pendingClaimsBadge(badge) {
  try {
    const pending = (await api("/admin/claims", { query: { status: "pending" } })).length;
    if (!pending) return;
    badge.textContent = pending > 99 ? "99+" : pending;
    badge.title = `${pending} player claim${pending === 1 ? "" : "s"} waiting for review`;
    badge.hidden = false;
  } catch { /* no badge */ }
}

// Anonymous page-view counter for the admin statistics (backend/delo/stats.py): page name and
// an external referring site only. Not sent for admins or with Do Not Track / Global Privacy Control.
function countVisit(user) {
  if (user?.isAdmin || navigator.doNotTrack === "1" || navigator.globalPrivacyControl) return;
  let referrer = "";
  try {
    if (document.referrer && new URL(document.referrer).host !== location.host) referrer = document.referrer;
  } catch { /* ignore malformed referrers */ }
  fetch(`${CONFIG.apiBase}/visit`, {
    method: "POST", keepalive: true, headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ page: location.pathname, referrer }),
  }).catch(() => {});
}
