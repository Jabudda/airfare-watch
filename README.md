# ✈️ Airfare Watch

Automated airline price tracker, built in the same pattern as
[crisis-management-scraper](https://jabudda.github.io/crisis-management-scraper/):
a scheduled scraper writes JSON data into the repo, and a GitHub Pages
dashboard displays it with filters and summary counters.

Every 6 hours the scraper pulls the **cheapest recently-seen fare** for each
route you track, compares it to that route's recent price history, and rates it:

| Rating | Meaning |
|---|---|
| 🟢 **Great deal** | ≥15% below the route's typical (median) price |
| 🟡 **Good / Fair** | around typical |
| 🔴 **High** | ≥15% above typical |

Trend arrows (▲▼) show movement since the previous scan.

## Data source

Fares come from the **Travelpayouts (Aviasales) Data API** — free, no credit
card, generous rate limits. It returns prices that real users recently found
in searches (cached up to ~7 days), not live bookable quotes. That's ideal
for spotting deals and price trends, which is what this dashboard is for.

> Note: Amadeus's free Self-Service API — the usual pick for projects like
> this — was decommissioned on July 17, 2026, and is now enterprise-only.

## How it works

```
routes.json ──► scraper.py ──► data/prices.json ──► index.html (GitHub Pages)
                    │
                    └──► data/history.json  (rolling price history per route)
```

- **`scraper.py`** — calls the Aviasales price-calendar API, no external
  Python dependencies.
- **`.github/workflows/update-prices.yml`** — runs the scraper every 6 hours
  and commits the updated data.
- **`index.html`** — static dashboard that fetches `data/prices.json`.

## Setup

1. **Get a free API token** (takes ~2 minutes)
   - Sign up at [travelpayouts.com](https://www.travelpayouts.com) (free
     affiliate network account, no credit card)
   - Copy your token from [app.travelpayouts.com/profile/api-token](https://app.travelpayouts.com/profile/api-token)

2. **Add the repo secret** — Settings → Secrets and variables → Actions:
   - `TRAVELPAYOUTS_TOKEN` = your token

3. **Enable GitHub Pages** — Settings → Pages → deploy from the `main`
   branch, root folder.

4. **Run it once** — Actions tab → "Update airfare data" → Run workflow.
   (Until then the dashboard shows the sample data included in `data/`.)

## Customizing routes

Edit `routes.json` — use IATA **city codes** for best coverage (CHI, NYC,
LON, LAX…); airport codes also work:

```json
{ "origin": "CHI", "destination": "LON", "label": "Chicago → London" }
```

Other knobs in `routes.json`:
- `days_ahead` — targets the month that many days out (default 21)
- `currency`, `max_history_per_route`

If a route returns "no cached fares," it means no Aviasales user searched it
recently — popular routes almost always have data; thin routes may be
intermittent.

## Local testing

```bash
python3 scraper.py --sample     # demo data, no token needed
python3 -m http.server          # then open http://localhost:8000
```

Ratings start as "Collecting data" until a route has 3+ price observations
(about 18 hours of scans) — the included sample data is pre-seeded so the
dashboard looks alive immediately.
