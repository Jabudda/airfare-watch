#!/usr/bin/env python3
"""
Airfare Watch — airline price scraper.

Pulls the cheapest current fare for each route in routes.json from the
Amadeus Self-Service API (free test environment), keeps a rolling price
history, rates each fare against its route's historical median, and writes
data/prices.json for the dashboard (index.html).

Usage:
    AMADEUS_CLIENT_ID=xxx AMADEUS_CLIENT_SECRET=yyy python3 scraper.py
    python3 scraper.py --sample        # generate realistic demo data, no API key needed

Get free API credentials at https://developers.amadeus.com (instant signup).
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

API_HOST = "https://test.api.amadeus.com"  # switch to https://api.amadeus.com for production


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


def http_post_form(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def http_get_json(url, params, token):
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full)
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


# ---------------------------------------------------------------- amadeus

def get_token():
    client_id = os.environ.get("AMADEUS_CLIENT_ID")
    client_secret = os.environ.get("AMADEUS_CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit(
            "Missing AMADEUS_CLIENT_ID / AMADEUS_CLIENT_SECRET environment variables.\n"
            "Get free credentials at https://developers.amadeus.com, or run with --sample."
        )
    resp = http_post_form(
        f"{API_HOST}/v1/security/oauth2/token",
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    return resp["access_token"]


def fetch_cheapest_offer(token, origin, destination, depart_date, adults, currency):
    """Return (price: float, carrier_code: str, nonstop: bool) or None."""
    try:
        resp = http_get_json(
            f"{API_HOST}/v2/shopping/flight-offers",
            {
                "originLocationCode": origin,
                "destinationLocationCode": destination,
                "departureDate": depart_date,
                "adults": adults,
                "currencyCode": currency,
                "max": 10,
            },
            token,
        )
    except Exception as e:  # noqa: BLE001 — log and keep scraping other routes
        print(f"  ! {origin}->{destination}: {e}", file=sys.stderr)
        return None

    offers = resp.get("data") or []
    if not offers:
        return None

    carriers = (resp.get("dictionaries") or {}).get("carriers", {})
    best = min(offers, key=lambda o: float(o["price"]["grandTotal"]))
    price = float(best["price"]["grandTotal"])
    itinerary = best["itineraries"][0]
    segments = itinerary["segments"]
    code = segments[0]["carrierCode"]
    airline = carriers.get(code, code)
    nonstop = len(segments) == 1
    return price, airline.title() if airline.isupper() and len(airline) > 3 else airline, nonstop


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
    "LAX": 128, "JFK": 112, "MIA": 98, "DEN": 84,
    "CUN": 215, "LHR": 480, "LAS": 76, "MCO": 92,
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
    adults = int(config.get("adults", 1))
    max_history = int(config.get("max_history_per_route", 60))

    depart_date = (datetime.now(timezone.utc) + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    history = load_json(HISTORY_FILE, {})
    token = None if args.sample else get_token()

    flights = []
    for route in config["routes"]:
        origin, dest = route["origin"], route["destination"]
        key = f"{origin}-{dest}"
        print(f"  {key} ...")

        if args.sample:
            # Seed believable history so ratings/trends show up in the demo.
            if key not in history:
                history[key] = [sample_observation(dest) for _ in range(10)]
            price = sample_observation(dest)
            airline = random.choice(SAMPLE_AIRLINES)
            nonstop = random.random() > 0.4
            result = (price, airline, nonstop)
        else:
            result = fetch_cheapest_offer(token, origin, dest, depart_date, adults, currency)

        if result is None:
            continue

        price, airline, nonstop = result
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
        "departure_date": depart_date,
        "currency": currency,
        "summary": summary,
        "flights": sorted(flights, key=lambda f: f["price"]),
    })
    save_json(HISTORY_FILE, history)
    print(f"Wrote {len(flights)} routes to {PRICES_FILE} ({len(rated)} rated).")


if __name__ == "__main__":
    main()
