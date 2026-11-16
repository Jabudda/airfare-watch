#!/usr/bin/env python3
"""
Airfare Watch — airline price scraper.

Pulls the cheapest recently-seen fare for each route in routes.json from the
Travelpayouts (Aviasales) Data API, keeps a rolling price history, rates each
fare against its route's historical median, and writes data/prices.json for
the dashboard (index.html).

Prices are cached fares from recent Aviasales user searches — real prices
people recently found, not live bookable quotes. Perfect for deal-watching.

Usage:
    TRAVELPAYOUTS_TOKEN=xxx python3 scraper.py
    python3 scraper.py --sample        # generate realistic demo data, no token needed

Get a free token: sign up at https://www.travelpayouts.com (free, no credit
card), then copy your token from https://app.travelpayouts.com/profile/api-token
"""

import argparse
import json
import os
import random
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
ROUTES_FILE = BASE_DIR / "routes.json"
PRICES_FILE = DATA_DIR / "prices.json"
HISTORY_FILE = DATA_DIR / "history.json"

API_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"

# Friendly names for common carrier codes (API returns IATA codes).
AIRLINES = {
    "UA": "United", "AA": "American", "DL": "Delta", "WN": "Southwest",
    "NK": "Spirit", "F9": "Frontier", "B6": "JetBlue", "AS": "Alaska",
    "BA": "British Airways", "VS": "Virgin Atlantic", "AC": "Air Canada",
    "AM": "Aeroméxico", "Y4": "Volaris", "VB": "Viva Aerobus",
    "IB": "Iberia", "AY": "Finnair", "LH": "Lufthansa", "AF": "Air France",
    "KL": "KLM", "EI": "Aer Lingus", "TP": "TAP Portugal",
}


# ---------------------------------------------------------------- helpers

def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def http_get_json(url, params):
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, headers={"User-Agent": "airfare-watch"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


# ---------------------------------------------------------------- fetch

def fetch_cheapest_fare(token, origin, destination, month, currency):
    """Return (price, airline, nonstop, depart_date) or None.

    Queries the Aviasales price-calendar cache for the cheapest fare found
    recently for this route in the given month (YYYY-MM).
    """
    try:
        resp = http_get_json(API_URL, {
            "origin": origin,
            "destination": destination,
            "departure_at": month,
            "one_way": "true",
            "sorting": "price",
            "limit": 10,
            "currency": currency.lower(),
            "token": token,
        })
    except Exception as e:  # noqa: BLE001 — log and keep scraping other routes
        print(f"  ! {origin}->{destination}: {e}", file=sys.stderr)
        return None

    offers = resp.get("data") or []
    if not offers:
        print(f"  - {origin}->{destination}: no cached fares", file=sys.stderr)
        return None

    best = min(offers, key=lambda o: float(o["price"]))
    price = float(best["price"])
    code = best.get("airline", "")
    airline = AIRLINES.get(code, code or "Various")
    nonstop = best.get("transfers", 1) == 0
    depart = (best.get("departure_at") or month)[:10]
    return price, airline, nonstop, depart


# ---------------------------------------------------------------- ratings

def rate(price, history_prices):
    """Rate a fare against the route's historical median."""
    if len(history_prices) < 3:
        return "unrated"
    med = median(history_prices)
    if price <= med * 0.85:
        return "great"
    if price <= med * 1.00:
        return "good"
    if price <= med * 1.15:
        return "fair"
    return "high"


def trend(price, history_prices):
    """Compare against the previous observation: up / down / flat."""
    if not history_prices:
        return "flat"
    prev = history_prices[-1]
    if prev == 0:
        return "flat"
    change = (price - prev) / prev
    if change > 0.02:
        return "up"
    if change < -0.02:
        return "down"
    return "flat"


# ---------------------------------------------------------------- sample mode

SAMPLE_AIRLINES = ["United", "American", "Delta", "Spirit", "Frontier", "British Airways"]

SAMPLE_BASE = {  # rough realistic one-way fares
    "LAX": 128, "NYC": 112, "MIA": 98, "DEN": 84,
    "CUN": 215, "LON": 480, "LAS": 76, "ORL": 92,
    "BKK": 720, "CNX": 790, "MEX": 240, "LIS": 440,
    "SJO": 310, "PTY": 330, "MDE": 360, "UIO": 420,
    "MNL": 820, "SGN": 870, "KUL": 850,
}


def sample_observation(dest):
    base = SAMPLE_BASE.get(dest, 150)
    return round(base * random.uniform(0.7, 1.4), 2)


# ---------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", action="store_true",
                        help="generate demo data without calling the API")
    args = parser.parse_args()

    config = load_json(ROUTES_FILE, None)
    if config is None:
        sys.exit(f"Could not read {ROUTES_FILE}")

    currency = config.get("currency", "USD")
    days_ahead = int(config.get("days_ahead", 21))
    max_history = int(config.get("max_history_per_route", 60))

    target = datetime.now(timezone.utc) + timedelta(days=days_ahead)
    month = target.strftime("%Y-%m")
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    token = None
    if not args.sample:
        token = os.environ.get("TRAVELPAYOUTS_TOKEN")
        if not token:
            sys.exit(
                "Missing TRAVELPAYOUTS_TOKEN environment variable.\n"
                "Get a free token at https://www.travelpayouts.com "
                "(profile → API token), or run with --sample."
            )

    history = load_json(HISTORY_FILE, {})

    flights = []
    for route in config["routes"]:
        origin, dest = route["origin"], route["destination"]
        key = f"{origin}-{dest}"
        print(f"  {key} ...")

        if args.sample:
            # Seed believable history so ratings/trends show up in the demo.
            if key not in history:
                history[key] = [sample_observation(dest) for _ in range(10)]
            result = (
                sample_observation(dest),
                random.choice(SAMPLE_AIRLINES),
                random.random() > 0.4,
                target.strftime("%Y-%m-%d"),
            )
        else:
            result = fetch_cheapest_fare(token, origin, dest, month, currency)

        if result is None:
            continue

        price, airline, nonstop, depart_date = result
        past = history.get(key, [])
        flights.append({
            "route": key,
            "label": route.get("label", f"{origin} → {dest}"),
            "origin": origin,
            "destination": dest,
            "price": price,
            "currency": currency,
            "airline": airline,
            "nonstop": nonstop,
            "departure_date": depart_date,
            "tier": route.get("tier", "other"),
            "rating": rate(price, past),
            "trend": trend(price, past),
            "median_price": round(median(past), 2) if len(past) >= 3 else None,
        })
        history[key] = (past + [price])[-max_history:]

    rated = [f for f in flights if f["rating"] != "unrated"]
    summary = {
        "routes_tracked": len(flights),
        "great_deals": sum(1 for f in flights if f["rating"] == "great"),
        "cheapest": min(flights, key=lambda f: f["price"])["price"] if flights else None,
        "average": round(sum(f["price"] for f in flights) / len(flights), 2) if flights else None,
    }

    save_json(PRICES_FILE, {
        "updated": now_iso,
        "departure_date": month,
        "currency": currency,
        "tiers": config.get("tiers", {}),
        "summary": summary,
        "flights": sorted(flights, key=lambda f: f["price"]),
    })
    save_json(HISTORY_FILE, history)
    print(f"Wrote {len(flights)} routes to {PRICES_FILE} ({len(rated)} rated).")


if __name__ == "__main__":
    main()
