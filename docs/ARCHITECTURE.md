# ESL — Architecture

ESL (Elo Scalp Lotion) tracks Elo ratings for Magic: The Gathering players from tournament results
uploaded by admins. It is designed to run for (close to) **$0/month** on AWS at
community scale.

## 1. Goals and non-goals

| Goals | Non-goals (for now) |
|-------|---------------------|
| Leaderboards for two ladders: **REL** and **REL + Casual** | Real-time pairing / running tournaments |
| Player detail page with stats, Elo chart, matchups, tournament history | Deck-level analytics (planned for paid tier) |
| Admin upload of tournament results in several text formats | Automatic scraping of third-party sites |
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

### 3.2 DynamoDB tables (all on-demand / PAY_PER_REQUEST)

**`Tournaments`**, PK `tournamentId`. Metadata only: name, date, type, sourceFormat,
s3Key, rawKey, playerCount, matchCount, uploadedAt, uploadedBy.

**`Players`**, PK `playerId`, SK:

| SK | Content |
|----|---------|
| `PROFILE` | displayName, hidden, membership, ownerSub (user-controlled fields survive recalculation) |
| `LADDER#rel` / `LADDER#all` | `ladder`, `rating`, `displayName`, `hidden`, `membership`, stats map, tournament history list |
| `HIST#rel#000001` … | one record **per match** per ladder: date, tournamentId, round, opponentId, result, score, ratingBefore, ratingAfter, delta |

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
draw = 0.5, byes ignored. Ratings update in round order. The model is versioned
(`MODEL_VERSION`), and a full recalculation takes seconds for thousands of matches.

Two independent ladders are computed on each recalculation:

* `rel` — only tournaments of type `rel`
* `all` — `rel` + `casual` (casual matches can be down-weighted with `casual_weight`)

## 5. API (API Gateway HTTP API → single Lambda)

| Method & path | Auth | Purpose |
|---------------|------|---------|
| `GET /api/leaderboard?ladder=rel\|all` | public | Sorted players |
| `GET /api/players/{id}?ladder=` | public | Stats + tournament history |
| `GET /api/players/{id}/history?ladder=` | public | Elo history (chart) |
| `GET /api/tournaments` · `GET /api/tournaments/{id}` | public | List / detail + standings |
| `GET /api/release-notes` · `GET /api/release-notes/{version}` | public | Docs page |
| `GET /api/elo-model` | public | Current model constants (Elo docs page) |
| `POST /api/tournaments` (`?dryRun=1` for preview) | admin | Upload results → recalc |
| `DELETE /api/tournaments/{id}` | admin | Remove tournament → recalc |
| `POST /api/admin/recalculate` | admin | Full recalculation |
| `POST /api/release-notes` | admin | Publish release note |
| `GET /api/admin/claims` · `POST /api/admin/claims/{sub}` | admin | Review profile claims |
| `POST /api/admin/players/{id}` | admin | Set hidden / membership |
| `GET /api/me` · `POST /api/me/claim` · `POST /api/me/privacy` | user | Account, claim profile, hide name |

API Gateway attaches the Cognito **JWT authorizer** to every non-GET route and to
`/api/me` and `/api/admin/*`. The Lambda additionally checks the `cognito:groups` claim
for `admin`.

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

## 7. Monetization (proposal, not implemented yet)

Uploads and viewing stay **free**. Paid tiers are cosmetic or add player-owned
content, never pay-to-see-your-rating (hiding ratings behind payment would erode trust
in the leaderboard, which is the whole product).

| Tier | Price | Perks |
|------|-------|-------|
| Free | $0 | Rating, stats, chart, claim profile, hide name |
| **Supporter** | $1/month (or $10/year) | Badge next to name, custom profile flair/colour, early access to features |
| **Diamond** | $5–10/month | Diamond badge, attach decklists to tournaments (Moxfield/Archidekt link), private match notes, matchup stats by archetype, CSV export of own history, custom profile page URL |
| Store/TO plan | $10–20/month per store | Branded store leaderboard page, embeddable widget for the store website, automatic upload connectors |

Other options: donations (Ko-fi / GitHub Sponsors), sponsored "season" leaderboards
with a store-funded prize, tasteful affiliate links to card shops on decklists.

Implementation sketch: Stripe Payment Links + a `POST /api/stripe/webhook` Lambda route
(signature-verified) that sets `membership` on the Account and the linked player
PROFILE. The `membership` field and badge rendering already exist in v1, and admins can
set it manually.

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
backend/esl/          Python package deployed as the Lambda
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
sample-data/          example uploads
```

## 10. Scaling notes

Full recalculation on every upload is O(all matches). This is fine up to tens of
thousands of matches: one Lambda invocation, and DynamoDB writes on the order of
matches × 2 ladders, which costs about $0.03 per 10k matches. If that becomes a
problem, add an incremental path: when the new tournament is the latest by date,
replay only it from the current ratings.
