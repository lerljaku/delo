# Decisions & open questions

Each question lists the default v1 was built with. Change it here and in the code if
you decide differently.

## Name

Name: **Delo**, at mtgdelo.com (renamed from ESL — Elo Scalp Lotion). EloHell (the working
name from the brief) and ManaRank were already taken. Other alternatives considered:

| Name | Notes |
|------|-------|
| Elodrazi / Black Elotus / Force of Elo / Mox Elo | "Elo" + MTG card wordplay |
| Swissboard / Tiebreaker / Grindboard | Tournament terms |
| Planeswalker Ladder | Very MTG-flavoured (check Wizards trademark usage) |
| Top8 Elo | Tournament-y |
| The Stack Rank | MTG pun ("the stack") |
| Mulligan Ratings | Fun, MTG-specific |
| Tapped Rating / Untap Elo | MTG verbs |
| Local Meta | Emphasises local store scene |

## Decisions taken (please confirm)

| # | Question | Default chosen |
|---|----------|----------------|
| 1 | Rate matches or individual games? | **Matches** (Bo3 result). Game W/L is still tracked for stats. |
| 2 | Starting rating, K-factor | 1500; K 48 for first 10 matches, then 32 |
| 3 | Do byes count? | Not rated, not in W/L stats; they count for tournament points/standings |
| 4 | Casual weight in REL + Casual ladder | 1.0 (same as REL). Could be 0.5. |
| 5 | Player identity across tournaments | Name match ignoring case, accents (Jiri = Jiří), emoji decorations and extra spaces. The most common spelling is displayed. Different spellings such as nicknames still create separate players. (Future: alias merge tool.) |
| 6 | "Best/worst matchup" definition | Opponent with the highest/lowest match score `(W + 0.5·D)/N`; at least 2 matches if any opponent qualifies |
| 7 | Win rate | `wins / (wins + losses + draws)` |
| 8 | Name hiding verification | Manual: email-verified account → claim profile → admin approves → owner can hide. Admins can hide on request. |
| 9 | Monetization | Not implemented. Badge field exists; tiers proposed in ARCHITECTURE.md §7 |
| 10 | Hosting | S3 + CloudFront + HTTP API + Lambda (Python) + DynamoDB + Cognito, Terraform |
| 11 | Tournament source data | S3 (normalized JSON + raw upload), not GitHub |
| 12 | AWS region | `eu-central-1` (variable) |
| 13 | Frontend tech | Plain HTML + vanilla JS, no build step; Chart.js from CDN |

## Open questions for you

1. **Domain**: mtgdelo.com, to be registered in Route 53. Terraform handles it once
   `domain_name` is set (see README → Custom domain).
2. **Event metadata**: all 47 files in `raw-data-eventlink/` import (53 events; `dl.txt`,
   `Najada a rytir.txt`, `najada + rytir.txt`, `najada + rytir3.6.txt` hold two events and
   `Vikend+najda+rytz.txt` three, split automatically by `backend/import_events.py`). Dates are guessed
   from file names, assuming the current year, and 14 files have no date at all. All events
   default to REL. The real names, dates and types go in `raw-data-eventlink/events.json`.
3. **Leagues / seasons**: should ratings reset each season, or should we show a
   season leaderboard in addition to all-time?
4. **Format split**: done. Per-format ladders for Modern, Limited, Duel Commander, EDH,
   Legacy, Vintage and Premodern next to the global ones. Tournaments uploaded before this
   have no format until an admin sets it on the tournament page (or `format` in events.json
   and `import_events.py --replace`).
5. **Commander / multiplayer pods**: Elo is 1v1. Multiplayer needs a different model
   (e.g. pairwise decomposition or TrueSkill). In scope?
6. **Minimum matches to appear on the leaderboard**: 1 rated match per ladder
   (`EloConfig.min_matches`; 10 was tried and reverted). Players below that are listed as
   "provisional" without rank or Elo. Ratings count from the first match either way.
7. **Who are the admins**: store owners/TOs? Should an admin only be able to upload
   for their own store (multi-tenant)?
8. **Payments**: Stripe OK? Monthly or yearly? Company/tax setup matters for selling in the EU.
9. **GitHub repo URL** for the "file a ticket" link (set in `frontend/config.js` /
   Terraform variable `github_repo`).
10. **GDPR**: done, but have the privacy notice (`frontend/js/privacy.js`) reviewed by someone
    who knows EU data protection law, and set `operator_name` and `privacy_contact` in
    `terraform.tfvars`. Implemented:
    - Privacy notice page, linked in the footer.
    - Users delete their own account (account record + Cognito login) on the account page.
    - Admins erase a player on request (Admin → Erase player): the name becomes
      "Deleted player" in every stored tournament, including the raw uploads and their old S3
      versions. Matches stay under a new random id, so other ratings don't change. The player
      id (a hash of the name) is kept on a suppression list so later uploads are anonymized
      automatically. DynamoDB point-in-time backups still hold the data for up to 35 days.
