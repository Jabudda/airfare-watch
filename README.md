# ✈️ Airfare Watch

Automated airline price tracker, built in the same pattern as
[crisis-management-scraper](https://jabudda.github.io/crisis-management-scraper/):
a scheduled scraper writes JSON data into the repo, and a GitHub Pages
dashboard displays it with filters and summary counters.

Every 6 hours the scraper pulls the **cheapest current fare** for each route
you track, compares it to that route's recent price history, and rates it:

| Rating | Meaning |
|---|---|
| 🟢 **Great deal** | ≥15% below the route's typical (median) price |
| 🟡 **Good / Fair** | around typical |
| 🔴 **High** | ≥15% above typical |

Trend arrows (▲▼) show movement since the previous scan.

## How it works

```
routes.json ──► scraper.py ──► data/prices.json ──► index.html (GitHub Pages)
                    │
                    └──► data/history.json  (rolling price history per route)
```

- **`scraper.py`** — calls the Amadeus Flight Offers Search API (free test
  environment), no external Python dependencies.
- **`.github/workflows/update-prices.yml`** — runs the scraper every 6 hours
  and commits the updated data.
- **`index.html`** — static dashboard that fetches `data/prices.json`.

## Setup

1. **Get free API credentials** (takes ~2 minutes)
   - Sign up at [developers.amadeus.com](https://developers.amadeus.com)
   - Create a Self-Service app → you get an **API Key** and **API Secret** instantly
   - The test environment is free with a monthly quota — plenty for a
     6-hourly scan of a handful of routes

2. **Create the GitHub repo** and push these files.

3. **Add repo secrets** — Settings → Secrets and variables → Actions:
   - `AMADEUS_CLIENT_ID` = your API Key
   - `AMADEUS_CLIENT_SECRET` = your API Secret

4. **Enable GitHub Pages** — Settings → Pages → deploy from the `main`
   branch, root folder.

5. **Run it once** — Actions tab → "Update airfare data" → Run workflow.
   (Until then the dashboard shows the sample data included in `data/`.)

## Customizing routes

Edit `routes.json` — each route is an origin/destination IATA airport code:

```json
{ "origin": "ORD", "destination": "LHR", "label": "Chicago → London" }
```

Other knobs in `routes.json`:
- `days_ahead` — how far out the departure date is (default 21)
- `currency`, `adults`, `max_history_per_route`

> **Test environment note:** Amadeus's free test tier uses a cached subset of
> real inventory. Large hubs (ORD, JFK, LAX, LON, PAR, MAD…) work well; small
> airports may return no results. For live production fares, switch
> `API_HOST` in `scraper.py` to `https://api.amadeus.com` and use production
> credentials (pay-as-you-go beyond the free quota).

## Local testing

```bash
python3 scraper.py --sample     # demo data, no API key needed
python3 -m http.server          # then open http://localhost:8000
```

Ratings start as "Collecting data" until a route has 3+ price observations
(about 18 hours of scans) — the included sample data is pre-seeded so the
dashboard looks alive immediately.
