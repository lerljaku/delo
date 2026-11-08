# Delo — Architecture

Delo (mtgdelo.com) tracks Elo ratings for Magic: The Gathering players from tournament results
uploaded by signed-in users (admins correct and delete them). It is designed to run for (close to) **$0/month** on AWS at
community scale.

## 1. Goals and non-goals

| Goals | Non-goals (for now) |
|-------|---------------------|
| Leaderboards for two ladders, **REL** and **REL + Casual**, for all formats and per format | Real-time pairing / running tournaments |
| Player detail page with stats, Elo chart, matchups, tournament history | Deck-level analytics (planned for paid tier) |
| Upload of tournament results by signed-in users, in several text formats | Automatic scraping of third-party sites |
| Full, deterministic **recalculation** when the Elo model changes | Multi-region / high availability |
| Player accounts (email-verified), profile claiming, name hiding | |
| Docs pages: release notes, Elo formula, link to GitHub issues | |

## 2. High-level diagram

```
                ┌────────────────────────── CloudFront (HTTPS, cache) ──────────────────────────┐
 Browser ──────►│  /*      → S3 "site" bucket (static HTML/JS/CSS, private, OAC)                │
                │  /api/*  → API Gateway HTTP API ──► Lambda "api" (Python 3.12, one function)  │
                └────────────────────────────────────────────────┬──────────────────────────────┘
                                                                 │
   Cognito User Pool (email sign-up/verify, "admin" group) ──JWT─┘ (authorizer on write routes)
                                                                 │
                         ┌───────────────────────────────────────┼─────────────────────────┐
                         ▼                                       ▼                         ▼
               S3 "data" bucket                        DynamoDB (on-demand)          DynamoDB
               raw/{id}.{ext}      (as uploaded)       Tournaments  (metadata)       Accounts
               tournaments/{id}.json (normalized,      Players      (profiles,       ReleaseNotes
                 source of truth for recalculation)                  ladders, Elo history)
               release-notes/{version}.md
```

### Why this stack (cost)

| Component | Free tier / cost at community scale |
|-----------|-------------------------------------|
| S3 (site + data) | Cents per month (a few MB) |
| CloudFront | 1 TB egress + 10M requests/month **always free** |
| API Gateway **HTTP API** | 1M requests/month free for 12 months, then $1 per million |
| Lambda | 1M requests + 400k GB-s/month **always free** |
| DynamoDB on-demand | 25 GB storage always free; pay per request (fractions of a cent here) |
| Cognito | 10k MAU free (Essentials/Lite tier) |
| Route 53 (optional custom domain) | $0.50/month per hosted zone + domain registration |

Estimated total: **$0–1/month** until traffic is substantial. The main alternative,
GitHub Pages for the frontend, saves nothing meaningful (S3 + CloudFront is already
~free) and splits the deployment across two providers, so everything lives in AWS.

Tournament source data lives in **S3**, not GitHub: it's cheaper to operate than a Git
repo used as a database (no tokens, rate limits, or merge conflicts). DynamoDB holds
queryable/derived data.

## 3. Data model

### 3.1 Source of truth: S3 `data` bucket

* `raw/{tournamentId}.{csv|json|txt}` — the exact upload (audit trail).
* `tournaments/{tournamentId}.json` — normalized tournament:

```json
{
  "id": "2026-09-12-fnm-modern-3f9a",
  "name": "FNM Modern",
  "date": "2026-09-12",
  "type": "rel",                    // "rel" | "casual"
  "format": "modern",               // game format (elo.GAME_FORMATS); null = all-formats ladders only
  "link": "https://...",            // optional public page of the event, or null
  "sourceFormat": "csv",
  "uploadedAt": "2026-09-13T10:00:00Z",
  "uploadedBy": "admin@example.com",
  "players": {"a1b2c3d4e5f6": "Alice Smith", "...": "..."},
  "matches": [
    {"round": 1, "p1": "a1b2c3d4e5f6", "p2": "0f9e8d7c6b5a", "p1Wins": 2, "p2Wins": 1, "draws": 0},
    {"round": 1, "p1": "112233445566", "p2": null, "p1Wins": 2, "p2Wins": 0, "draws": 0}  // bye
  ],
  "standings": [{"rank": 1, "playerId": "...", "points": 9, "wins": 3, "losses": 0, "draws": 0, "byes": 0, "omw": 0.55, "gw": 0.8}]
}
```

**Recalculation** = read every `tournaments/*.json`, sort by `(date, uploadedAt, id)`,
replay all matches through the Elo model, rewrite derived DynamoDB items. Changing the
model is therefore a code change + `POST /api/admin/recalculate`.

**Uploads are incremental**: when the new tournament sorts after every stored one (the usual
case), only it is replayed, starting from its players' stored ladder entries, whose `state`
holds the unrounded rating, current streaks and head-to-head counts. Only the touched
`LADDER#` items are rewritten, and new `HIST#` items are appended. This gives exactly the result
of a full recalculation (tested). A backdated tournament, or stored entries from an older
`MODEL_VERSION`, trigger a full recalculation instead. Deleting a tournament or changing its
format always recalculates everything.

### 3.2 DynamoDB tables (all on-demand / PAY_PER_REQUEST)

**`Tournaments`**, PK `tournamentId`. Metadata only: name, date, type, format, link, decks (`{playerId: deck name}`, set by players, not part of the tournament JSON because it doesn't affect ratings), sourceFormat,
s3Key, rawKey, playerCount, matchCount, uploadedAt, uploadedBy.

**`Players`**, PK `playerId`, SK:

| SK | Content |
|----|---------|
| `PROFILE` | displayName, spellings (name counts for picking displayName), hidden, membership, ownerSub (user-controlled fields survive recalculation) |
| `LADDER#rel`, `LADDER#all`, `LADDER#rel:modern`, … | `ladder`, `rating`, `displayName`, `hidden`, `membership`, stats map, tournament history list, engine `state` |
| `HIST#rel#000001`, `HIST#rel:modern#000001` … | one record **per match** per ladder: date, tournamentId, round, opponentId, result, score, ratingBefore, ratingAfter, delta |
| `ERASED` | marker only: this player id was erased (GDPR), anonymize it in new uploads |
| `ALIAS` | `target`: this player id (a misspelling) was merged into `target`; new uploads use `target` |

GSI `leaderboard`: PK `ladder` (S), SK `rating` (N) → one query returns a sorted leaderboard.

`playerId` = first 12 hex chars of `sha256(casefolded, whitespace-normalized name)`. It is
opaque, so hiding a name doesn't leak it through URLs.

**`Accounts`**, PK `sub` (Cognito user id): email, playerId, claimStatus
(`none|pending|approved|rejected`), membership, timestamps.

**`ReleaseNotes`**, PK `version`: title, date, summary, s3Key. Body markdown lives in
S3 `release-notes/{version}.md`.

## 4. Elo model (v1)

See [ELO.md](ELO.md) for the formula. Summary: standard Elo, start 1500, K = 48 for a
player's first 10 matches and 32 afterward, **match** result (not individual games),
draws and byes not rated. Ratings update in round order. The model is versioned
(`MODEL_VERSION`), and a full recalculation takes seconds for thousands of matches.

Independent ladders are computed on each recalculation:

* `rel` — only tournaments of type `rel`
* `all` — `rel` + `casual` (casual matches can be down-weighted with `casual_weight`)
* `rel:<format>` and `all:<format>` — the same, limited to one game format (`modern`,
  `limited`, `duel-commander`, `edh`, `legacy`, `vintage`, `premodern`). `rel` and `all`
  remain the global ladders across all formats.

**Islands.** An island is a group of players who play each other but not other groups, usually a
country. Ratings from islands that never meet can't be compared, so every ladder above exists per
island, with ids `<island>/<ladder>[:<format>]` (e.g. `czech-island/rel:modern`), rated only from that
island's tournaments. Tournaments and upcoming tournaments carry an `island`; islands are stored in
the Players table under partition `_ISLANDS` and listed oldest first, the first being the default.
The site keeps the selected island in a `delo_island` cookie and sends `?island=` with every API
call; a missing or unknown island means the default. Data without an island (from before islands)
uses the plain ladder ids; `backend/migrate_islands.py` moves it into "Czech Island" and recalculates.

## 5. API (API Gateway HTTP API → single Lambda)

| Method & path | Auth | Purpose |
|---------------|------|---------|
| `GET /api/leaderboard?ladder=rel\|all&format=` | public | Sorted players (no `format` = all formats) |
| `GET /api/players/{id}?ladder=&format=` | public | Stats + tournament history |
| `GET /api/players/{id}/history?ladder=&format=` | public | Elo history (chart) |
| `GET /api/tournaments` · `GET /api/tournaments/{id}` | public | List / detail + standings and each player's Elo change (`ratingChanges`) |
| `GET /api/release-notes` · `GET /api/release-notes/{version}` | public | Docs page |
| `GET /api/elo-model` | public | Current model constants (Elo docs page) |
| `POST /api/tournaments?replace={id}` | admin | Corrected results: saves the new tournament, keeps players' decks, deletes the old one, full recalc |
| `POST /api/tournaments` (`?dryRun=1` for preview) | user (`?replace=` admin) | Upload results (format required, link optional; `files: [{name, content}]` combines Eventlink exports, e.g. one per round, in round order) → incremental rating update |
| `POST /api/tournaments/{id}` | admin | Change format and/or link (format change → recalc) |
| `DELETE /api/tournaments/{id}` | admin | Remove tournament → recalc |
| `POST /api/admin/recalculate` | admin | Full recalculation |
| `POST /api/release-notes` | admin | Publish release note |
| `GET /api/admin/claims` · `POST /api/admin/claims/{sub}` | admin | Review profile claims |
| `POST /api/admin/players/{id}` | admin | Set hidden / membership |
| `POST /api/admin/players/{id}/erase` | admin | GDPR erasure: anonymize the player everywhere (see `delo/privacy.py`) |
| `POST /api/admin/players/{id}/merge` (`?dryRun=1` to preview) | admin | Merge a misspelled player into `into`: rewrites their tournaments, moves decks, remembers the alias, full recalc; refused if both played in one tournament or the misspelled one is claimed |
| `GET /api/me` · `POST /api/me/claim` · `POST /api/me/privacy` | user | Account, claim profile, hide name |
| `DELETE /api/me` | user | Delete own account and Cognito login |
| `GET /api/me/decks` · `POST /api/me/decks/{tournamentId}` | user (approved claim) | Own tournaments with decks + suggestions per format (own decks newest first, then others); set or clear a deck |
| `POST /api/me/profile` | user (approved claim) | Flair + colour (anyone), custom profile URL `slug` (Diamond) |
| `GET /api/me/notes` · `POST /api/me/notes/{tournamentId}` | user (Diamond to write) | Private match notes |
| `GET /api/me/archetypes` · `GET /api/me/export` | user (Diamond) | Matchups by deck; own history as CSV |
| `GET /api/upcoming` · `POST /api/upcoming` · `DELETE /api/upcoming/{id}` · `GET /api/me/upcoming` | public / Diamond or admin / poster or admin / user | Upcoming tournaments |
| `GET /api/admin/tournaments/{id}` | admin | Who uploaded a tournament (`uploadedBy` is an email, so it is left out of the public tournament data) |
| `GET /api/me/planned-tournaments` · `GET /api/planned-tournaments?days=` | user / public | Upload form picker: planned tournaments of the last 30 days and the next date of each, oldest first. `?days=N` (the Chrome extension: 14): only the last N days, up to today |
| `GET /api/players/by-slug/{name}` | public | Resolve a custom profile URL |
| `GET /api/islands` · `GET /api/admin/islands` · `POST /api/admin/islands` · `POST /api/admin/islands/{id}` | public / admin | Islands for the navigation picker; admin list with tournament counts, create, rename |
| `POST /api/visit` · `GET /api/admin/stats?days=` | public / admin | Cookie-free page-view counter (`delo/stats.py`); daily visitors, page views, top pages and referrers |
| `POST /api/stripe/webhook` | Stripe signature | Membership from payments (§7) |
| `POST /api/eventlink` | public (throttled) | Chrome extension upload (`extension/`): one tournament per Eventlink event, replaced when its results change |
| `GET /api/jobs/{id}` | public (the id is a 128-bit secret) | Progress of a background request: `queued` · `running` · `done` with `statusCode` + `result` |

API Gateway attaches the Cognito **JWT authorizer** to every non-GET route and to
`/api/me` and `/api/admin/*`. The Lambda additionally checks the `cognito:groups` claim
for `admin`.

**Background jobs.** API Gateway ends a request after 30 s, and recalculating every rating
can take longer. Routes that can recalculate (upload, extension upload, edit/delete tournament,
recalculate, erase, merge) accept `?async=1`. With it, the request is checked (auth) and saved
as a job (players-table item `_JOB#<id>`, deleted after a day by TTL), and the API answers
`202 {jobId}`. The Lambda then invokes itself asynchronously (`{"jobId": …}`, no retries), and
that invocation answers the saved request with up to 180 s to do it. The site polls
`GET /api/jobs/{id}` every 2 s (`api(…, {background: true})` in `common.js`). A job runs at
most once (conditional queued → running). One that hasn't finished well past the Lambda
timeout is reported as failed. Locally, jobs run in a thread of the dev server.

CloudFront serves the API under the same origin (`/api/*`), so there's **no CORS** and
one URL for everything.

## 6. Identity, claiming and hiding names

1. A user signs up through the Cognito Hosted UI, which verifies the email.
2. On the Account page they pick "this is me" → claim becomes `pending`.
3. An admin approves the claim, typically after checking in person at the store or by
   comparing the email with the one registered with the tournament organizer. The
   player is now linked to the account.
4. The owner of an approved profile can toggle **Hide my name**. Hidden players render
   as `Hidden player` everywhere (leaderboard, opponents in other players' history,
   standings). Admins can also hide a player directly, for example after an emailed
   request.

Identity verification is deliberately **manual via admin approval** for v1. Anyone
can type any name into a claim form, so an automated check isn't meaningful without
an external identity source such as a Wizards account, which has no public API.

## 7. Monetization

Uploads and viewing stay **free**. Paid tiers are cosmetic or add player-owned
content, never pay-to-see-your-rating (hiding ratings behind payment would erode trust
in the leaderboard, which is the whole product).

| Tier | Price | Perks | Status |
|------|-------|-------|--------|
| Free | $0 | Rating, stats, chart, claim profile, hide name, record decks, profile flair with a colour | live |
| **Supporter** | $5 once | Badge next to name, early access to features | live (no early-access features yet) |
| **Diamond** | $5/month | Diamond badge, private match notes, matchups by opponent's deck, CSV export of own history, custom profile URL (`player.html?u=<name>`), posting upcoming tournaments | live |
| Store/TO plan | $10/month per store | Branded store leaderboard page, embeddable widget for the store website, automatic upload connectors | not built (needs a store entity and §8) |

Other options: donations (Ko-fi / GitHub Sponsors), sponsored "season" leaderboards
with a store-funded prize, tasteful affiliate links to card shops on decklists.

**Payments**: Stripe Payment Links, no Stripe SDK. The membership page opens the link with
`client_reference_id=<Cognito sub>`. Stripe calls `POST /api/stripe/webhook` (a public API
Gateway route; the Lambda verifies the `Stripe-Signature` HMAC with `STRIPE_WEBHOOK_SECRET`,
see `delo/payments.py`):

* `checkout.session.completed`: `mode: payment` → `supporter: true` (lifetime),
  `mode: subscription` → `diamond: true`; stores `stripeCustomerId`.
* `customer.subscription.created|updated|deleted`: `diamond` = subscription status is
  active / trialing / past_due (and not deleted). Cancelling in the customer portal keeps
  the subscription active until the end of the paid period.

The Account's `membership` is Diamond if `diamond`, else Supporter if `supporter`; it is copied
to the claimed player PROFILE (badge). Perks check the higher of the paid membership and one
an admin set on the profile; admins have every perk (`App._level`, `delo/premium.py`).
Notes stay readable after a membership ends; new paid content can't be added.

**Upcoming tournaments** live in the Players table under partition `_UPCOMING` (single-table).
One-off events carry a DynamoDB TTL attribute `expiresAt` and disappear 30 days after the event
(the public list shows only future dates; the upload form offers the last 30 days). Events can
**recur** weekly (same weekday) or monthly (same day of the month, last day in shorter months):
they have no TTL, and the list shows only their next date, with a "might be cancelled" note when
that day is a Czech public holiday (`delo/schedule.py`, Easter computed). Diamond members can list
up to 10 at a time; the poster or an admin can remove one.

## 8. Connectors (future)

* **Eventlink (Wizards)**: no public API. Supported today by copying the pairings and
  pasting them into the upload form (the `eventlink` parser, built from real exports).
  Scraping or reverse engineering the API would violate the Terms of Service.
* **Melee.gg**: has public tournament pages with standings and pairings, and exports.
  A connector could import by tournament ID. Check their ToS and whether an API key is
  needed first.
* **Companion (MTG Companion app)**: no public API. Same approach as Eventlink.
* **Topdeck.gg / Spicerack**: have public APIs for some events and are good candidates
  for a connector.

Connectors would be implemented as parsers producing the same normalized tournament
JSON, so the rest of the pipeline is unchanged.

## 9. Repository layout

```
backend/delo/          Python package deployed as the Lambda
  elo.py              rating model + constants
  parsers.py          upload formats → normalized tournament
  engine.py           replay tournaments → ladders, stats, history
  storage.py          LocalStorage (dev) and AwsStorage (S3 + DynamoDB)
  api.py              HTTP router / Lambda handler
backend/tests/        unit tests (stdlib unittest)
backend/local_server.py  run the whole site locally without AWS
frontend/             static site (vanilla JS, Chart.js from CDN)
infra/                Terraform
docs/                 architecture, decisions, Elo docs, release notes
```

## 10. Scaling notes

An upload of the latest tournament costs O(its players): about 4 ladders × players reads
and writes. A full recalculation (backdated upload, delete, format change, model change) is
O(all matches): one Lambda invocation, and DynamoDB writes on the order of matches × 4
ladders (global and format, each REL and REL + Casual), about $0.06 per 10k matches.
