# mtgdelo: upload from Eventlink (Chrome extension)

Uploads an Eventlink tournament's results to mtgdelo in one click, instead of copying the pairings
of every round into the upload page.

- Reads the results in the Eventlink tab where the user is signed in, in one hidden frame of
  that tab, so the Eventlink session never leaves the browser. The frame loads the first round's
  pairings page, then switches rounds inside the running Eventlink app (10 s per round). If a
  round doesn't show up, it loads that page afresh (30 s, tried twice).
- Uploads to `POST /api/eventlink`, which needs no mtgdelo account (see docs/DECISIONS.md, "Who
  uploads").
- The server keeps one tournament per Eventlink event. Clicking Upload again with the same
  results changes nothing. After more rounds have been played, it replaces the earlier upload
  and recalculates.
- Reads every round in the event's sidebar: the Swiss rounds and the playoff ones (Quarterfinal,
  Semifinal, Final, Top N). Swiss rounds link to `…/rounds/<n>/standings`, playoff ones just to
  `…/rounds/<n>`. If the sidebar shows more started rounds than it found links for, nothing is
  uploaded.
- Refuses events with unfinished matches. Also refuses a page that still shows another round's
  results (checked against the round name the page shows, e.g. "Round 3" or "Final"; the
  players' W–L–D records only when a page has none, since they can lag: the Final's records
  leave the final out).

## Install (unpacked)

1. Open `chrome://extensions`, turn on **Developer mode**.
2. **Load unpacked** → pick this `extension/` folder. Or, from the site: download
   `mtgdelo-extension.zip` at the bottom of the upload page (published by `terraform apply`,
   `infra/site.tf`), unzip it and pick that folder.
3. Open a tournament in Eventlink (any of its pages), click the mtgdelo toolbar button, check the
   details and click **Upload**.

The extension talks to https://mtgdelo.com by default. For a local server
(`py backend/local_server.py`), set **Settings → mtgdelo site** to `http://localhost:8000`.
Other sites need adding to `host_permissions` in `manifest.json`.

Eventlink pages don't show the event's date, so the popup asks for it (today by default). The
details picked are remembered per event for the next upload. **Planned tournament** lists the
site's upcoming tournaments that took place in the last 2 weeks (each date of a recurring
one), for the selected island. Picking one fills in its name, date, format and type.

The formats in `popup.js` mirror `GAME_FORMATS` in `backend/delo/elo.py`. Keep them in sync.

The icons in `icons/` are the site's favicon (`frontend/favicon.svg`, the 👑 emoji) rendered to
PNG, since Chrome doesn't take SVG icons. Re-render them if the favicon changes.
