# Decisions & open questions

Each question lists the default v1 was built with. Change it here and in the code if
you decide differently.

## Name

Working name: **EloHell** (from the brief). Alternatives:

| Name | Notes |
|------|-------|
| EloHell | Memorable, self-deprecating ("stuck in Elo hell"); `elohell.gg` style domain |
| ManaRank | Clear MTG + ranking association |
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

1. **Name & domain**: which name, and do you own a domain? (Custom domain needs an ACM
   certificate in `us-east-1` and Route 53 or your DNS provider.)
2. **Upload format**: answered by the files in `raw-data-eventlink/`. The `eventlink` parser imports 42 of
   the 47 files. The other 5 (`dl.txt`, `Najada a rytir.txt`, `najada + rytir.txt`,
   `najada + rytir3.6.txt`, `Vikend+najda+rytz.txt`) contain two events pasted into one file and
   must be split. Is that expected? What are the dates, REL/casual types and exact names of these events? The
   filenames only hint at them (e.g. `25.2. DC` = Duel Commander on 25 February, but which year?).
3. **Leagues / seasons**: should ratings reset each season, or should we show a
   season leaderboard in addition to all-time?
4. **Format split**: separate ladders per format (Modern, Commander, Limited…)? The
   schema allows it (ladder = key), but it multiplies the UI.
5. **Commander / multiplayer pods**: Elo is 1v1. Multiplayer needs a different model
   (e.g. pairwise decomposition or TrueSkill). In scope?
6. **Minimum matches to appear on the leaderboard**: currently 1. Common choice is 5–10
   (players below that shown as "provisional").
7. **Who are the admins**: store owners/TOs? Should an admin only be able to upload
   for their own store (multi-tenant)?
8. **Payments**: Stripe OK? Monthly or yearly? Company/tax setup matters for selling in the EU.
9. **GitHub repo URL** for the "file a ticket" link (set in `frontend/config.js` /
   Terraform variable `github_repo`).
10. **GDPR**: EU players. The hide-name feature helps, but we should also publish a privacy
    notice and support full deletion requests (admin can hide today; a full delete
    requires editing the source tournaments).
