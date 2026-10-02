# Elo calculation (model v1.0)

The website's "Elo formula" page renders the same content with live constants from
`GET /api/elo-model`.

## Expected score

For player A (rating $R_A$) against player B (rating $R_B$):

$$E_A = \frac{1}{1 + 10^{(R_B - R_A)/400}}$$

$E_A$ is the probability-like expectation that A wins. A 200-point gap gives the
stronger player about 76%.

## Rating update

$$R'_A = R_A + K_A \cdot w \cdot (S_A - E_A)$$

* $S_A$ = 1 for a match win, 0.5 for a draw, 0 for a loss. **Matches** are rated, not
  individual games: 2-1 and 2-0 are both a win.
* $K_A$ = **48** while A has played fewer than **10** rated matches (provisional), then **32**.
* $w$ = weight: 1.0 for REL matches. For casual matches in the "REL + Casual" ladder,
  `casual_weight` (currently 1.0) applies.
* Everyone starts at **1500**.
* **Byes are not rated.** Unfinished or unreported matches should be left out of the upload.

Because K can differ between the two players (provisional vs. established), a single
match is not always exactly zero-sum.

## Ordering

Tournaments are replayed in order of date, then upload time. Within a tournament,
matches are processed round by round.

## Ladders

* **REL**: only tournaments marked REL (Competitive/Professional, or Regular with prizes).
* **REL + Casual**: every tournament.

Both ladders also exist per game format (Modern, Limited, Duel Commander, EDH, Legacy,
Vintage, Premodern), using only that format's tournaments. The plain REL and REL + Casual
ladders stay global (every format). All ladders are computed independently, so a player has
one rating per ladder they have played in.

## Provisional players

A player needs **1** rated match in a ladder (byes don't count) to get a rank and a visible
Elo there (`min_matches`). Players with only byes are listed as provisional, without rank or
rating. Raising the threshold (e.g. to 10) is a display rule: ratings count from the first
match either way, so changing it needs no recalculation.

## Recalculation

Raw results are stored permanently. When any constant or rule changes, `MODEL_VERSION`
is bumped and all ratings are recomputed from scratch, so every rating is always
consistent with the current model.

A new upload dated after every rated tournament is rated on top of the current ratings,
which gives the same numbers as a full replay. A backdated upload triggers a full
recalculation, because Elo depends on the order of matches.
