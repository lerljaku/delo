// "How ratings are calculated" section of the Docs page, with the live model constants.
import { api, esc, showError } from "./common.js";

function expected(diff, scale) {
  return 1 / (1 + 10 ** (-diff / scale));
}

export function renderEloFormula(content) {
  return api("/elo-model").then((m) => {
  const examples = [0, 99, 200, 305, 418, 542].map((d) => `<tr><td class="num">${d}</td><td class="num">${(expected(d, m.scale) * 100).toFixed(1)}%</td>
    <td class="num">+${(m.k_factor * (1 - expected(d, m.scale))).toFixed(1)}</td><td class="num">−${(m.k_factor * expected(d, m.scale)).toFixed(1)}</td></tr>`).join("");
  content.innerHTML = `
    <p class="secondary">Model version ${esc(m.modelVersion)}. Ratings are always recalculated from all stored results, so everyone is rated by the same model.</p>

    <h3>1. Expected score</h3>
    <p>Before a match between player A (rating R<sub>A</sub>) and player B (rating R<sub>B</sub>), A's expected score is</p>
    <p class="formula">E<sub>A</sub> = 1 / (1 + 10<sup>(R<sub>B</sub> − R<sub>A</sub>) / ${m.scale}</sup>)</p>
    <p>The number ${m.scale} is chosen so that <strong>a 200-point rating difference means a 60% chance to win</strong> the match.
      Chess uses 400, which makes a 400-point favourite win 10 times out of 11; Magic has too much variance for that. The same
      normalisation is used by the MTG Elo Project.</p>

    <h3>2. Rating update</h3>
    <p class="formula">R′<sub>A</sub> = R<sub>A</sub> + K · w · (S<sub>A</sub> − E<sub>A</sub>)</p>
    <ul>
      <li><strong>S<sub>A</sub></strong>: 1 for a match win, 0 for a loss. The <em>match</em> result counts, not individual games, so 2-0 and 2-1 are both a win.</li>
      <li><strong>Draws are not rated</strong>: neither player's rating changes. Many draws are intentional, for example both players drawing
        into the top 8, so they say nothing about who is better. Draws still show in the W-L-D record.</li>
      <li><strong>K = ${m.provisional_k_factor}</strong> for a player's first ${m.provisional_matches} rated matches (wins and losses; the provisional period), then <strong>K = ${m.k_factor}</strong>.</li>
      <li><strong>w</strong> is the match weight: 1 for REL, ${m.casual_weight} for casual matches in the REL + Casual ladder.</li>
      <li>Everyone starts at <strong>${m.initial_rating}</strong>. Byes are not rated.</li>
    </ul>

    <h3>3. What that means in practice</h3>
    <p class="secondary">For established players (K = ${m.k_factor}):</p>
    <div class="table-wrap" style="max-width:560px"><table><thead><tr><th class="num">Rating advantage</th><th class="num">Expected score</th>
      <th class="num">Favourite wins</th><th class="num">Favourite loses</th></tr></thead><tbody>${examples}</tbody></table></div>

    <h3>4. Ladders</h3>
    <p><strong>REL</strong> includes only REL tournaments (official events with prizes and entry fees).
      <strong>REL + Casual</strong> also includes casual events. Both exist for all formats together and for each format
      (Modern, Limited, Duel Commander, EDH, Legacy, Vintage, Premodern) on its own. Each ladder is calculated independently, so a player
      who plays Modern and Legacy has separate Modern and Legacy ratings plus an all-formats rating.</p>
    <p>A player gets a rank and a visible Elo in a ladder after <strong>${m.min_matches} rated match${m.min_matches === 1 ? "" : "es"}</strong> there
      (byes don't count). Until then they are listed as provisional. Their rating is calculated from the first match, so it is already
      settled when it appears.</p>

    <h3>5. Order of matches</h3>
    <p>Tournaments are processed by date, and matches inside a tournament by round. A newly uploaded tournament is rated on top of the
      current ratings. If it is dated before a tournament that is already rated, all tournaments are recalculated in date order instead.</p>`;
}).catch((err) => showError(content, err));
}
