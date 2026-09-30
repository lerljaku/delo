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

The two ladders are computed independently, so the same player has two ratings.

## Recalculation

Raw results are stored permanently. When any constant or rule changes, `MODEL_VERSION`
is bumped and all ratings are recomputed from scratch, so every rating is always
consistent with the current model.
