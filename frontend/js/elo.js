import { api, esc, renderNav, showError } from "./common.js";

renderNav("elo.html");
const content = document.getElementById("content");

function expected(diff, scale) {
  return 1 / (1 + 10 ** (-diff / scale));
}

api("/elo-model").then((m) => {
  const examples = [0, 100, 200, 400].map((d) => `<tr><td class="num">${d}</td><td class="num">${(expected(d, m.scale) * 100).toFixed(0)}%</td>
    <td class="num">+${(m.k_factor * (1 - expected(d, m.scale))).toFixed(1)}</td><td class="num">−${(m.k_factor * expected(d, m.scale)).toFixed(1)}</td></tr>`).join("");
  content.innerHTML = `
    <p class="secondary">Model version ${esc(m.modelVersion)}. Ratings are always recalculated from all stored results, so everyone is rated by the same model.</p>

    <h2>1. Expected score</h2>
    <p>Before a match between player A (rating R<sub>A</sub>) and player B (rating R<sub>B</sub>), A's expected score is</p>
    <p class="formula">E<sub>A</sub> = 1 / (1 + 10<sup>(R<sub>B</sub> − R<sub>A</sub>) / ${m.scale}</sup>)</p>

    <h2>2. Rating update</h2>
    <p class="formula">R′<sub>A</sub> = R<sub>A</sub> + K · w · (S<sub>A</sub> − E<sub>A</sub>)</p>
    <ul>
      <li><strong>S<sub>A</sub></strong>: 1 for a match win, 0.5 for a draw, 0 for a loss. The <em>match</em> result counts, not individual games, so 2-0 and 2-1 are both a win.</li>
      <li><strong>K = ${m.provisional_k_factor}</strong> for a player's first ${m.provisional_matches} rated matches (provisional period), then <strong>K = ${m.k_factor}</strong>.</li>
      <li><strong>w</strong> is the match weight: 1 for REL, ${m.casual_weight} for casual matches in the REL + Casual ladder.</li>
      <li>Everyone starts at <strong>${m.initial_rating}</strong>. Byes are not rated.</li>
    </ul>

    <h2>3. What that means in practice</h2>
    <p class="secondary">For established players (K = ${m.k_factor}):</p>
    <div class="table-wrap" style="max-width:560px"><table><thead><tr><th class="num">Rating advantage</th><th class="num">Expected score</th>
      <th class="num">Favourite wins</th><th class="num">Favourite loses</th></tr></thead><tbody>${examples}</tbody></table></div>

    <h2>4. Ladders</h2>
    <p><strong>REL</strong> includes only REL tournaments (official events with prizes and entry fees).
      <strong>REL + Casual</strong> also includes casual events. Each ladder is calculated independently, so a player has two ratings.</p>

    <h2>5. Order of matches</h2>
    <p>Tournaments are processed by date, and matches inside a tournament by round.</p>`;
}).catch((err) => showError(content, err));
