// Deck picker shared by the account page and the player's own page: a text input per
// tournament with a <datalist> of suggestions per format. The server orders suggestions
// (own decks, most recently played first, then other players' decks in the format).
import { api, currentUser, esc } from "./common.js";

const listId = (format) => `decks-${format || "none"}`;
const options = (names) => (names || []).map((d) => `<option value="${esc(d)}"></option>`).join("");

// The signed-in user's tournaments, decks and suggestions, or null if they have no approved
// player profile (or aren't signed in).
export async function loadOwnDecks() {
  if (!currentUser()) return null;
  try {
    return await api("/me/decks");
  } catch (err) {
    if (err.status === 401 || err.status === 403) return null;
    throw err;
  }
}

// One <datalist> per format that appears in `tournaments`.
export function deckDatalists(data) {
  const formats = new Set(data.tournaments.map((t) => t.format || ""));
  return [...formats].map((f) => `<datalist id="${listId(f)}">${options(data.suggestions[f])}</datalist>`).join("");
}

export function deckInput(t, deck) {
  return `<span class="deck-fields"><input type="text" class="deck-input" list="${listId(t.format)}" maxlength="60"
      value="${esc(deck || "")}" data-tid="${esc(t.tournamentId)}" placeholder="Choose or type a deck" aria-label="Deck for ${esc(t.name)}">
    <span class="deck-status muted" aria-live="polite"></span></span>`;
}

export const deckLabel = (deck) => (deck ? `<span class="deck" title="Deck">${esc(deck)}</span>` : "");

// Saves on change, then refreshes the suggestions so the deck moves to the top of its list.
// onSaved(tournamentId, deck) runs after each successful save.
export function bindDeckInputs(root, onSaved = () => {}) {
  root.querySelectorAll("input.deck-input").forEach((input) => input.addEventListener("change", async () => {
    const status = input.closest(".deck-fields").querySelector(".deck-status");
    status.textContent = "Saving…";
    try {
      const r = await api(`/me/decks/${encodeURIComponent(input.dataset.tid)}`, { method: "POST", body: { deck: input.value } });
      input.value = r.deck || "";
      status.textContent = "Saved";
      onSaved(input.dataset.tid, r.deck);
      const fresh = await api("/me/decks");
      root.querySelectorAll("datalist[id^='decks-']").forEach((list) => {
        list.innerHTML = options(fresh.suggestions[list.id === "decks-none" ? "" : list.id.slice("decks-".length)]);
      });
    } catch (err) {
      status.textContent = err.message;
    }
  }));
}
