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

# Friendly names and websites for carrier codes (API returns IATA codes).
AIRLINES = {
    "UA": ("United", "https://www.united.com"),
    "AA": ("American", "https://www.aa.com"),
    "DL": ("Delta", "https://www.delta.com"),
    "WN": ("Southwest", "https://www.southwest.com"),
    "NK": ("Spirit", "https://www.spirit.com"),
    "F9": ("Frontier", "https://www.flyfrontier.com"),
    "B6": ("JetBlue", "https://www.jetblue.com"),
    "AS": ("Alaska", "https://www.alaskaair.com"),
    "G4": ("Allegiant", "https://www.allegiantair.com"),
    "SY": ("Sun Country", "https://www.suncountry.com"),
    "BA": ("British Airways", "https://www.britishairways.com"),
    "VS": ("Virgin Atlantic", "https://www.virginatlantic.com"),
    "AC": ("Air Canada", "https://www.aircanada.com"),
    "AM": ("Aeroméxico", "https://www.aeromexico.com"),
    "Y4": ("Volaris", "https://www.volaris.com"),
    "VB": ("Viva Aerobus", "https://www.vivaaerobus.com"),
    "CM": ("Copa Airlines", "https://www.copaair.com"),
    "AV": ("Avianca", "https://www.avianca.com"),
    "LA": ("LATAM", "https://www.latamairlines.com"),
    "IB": ("Iberia", "https://www.iberia.com"),
    "AY": ("Finnair", "https://www.finnair.com"),
    "LH": ("Lufthansa", "https://www.lufthansa.com"),
    "AF": ("Air France", "https://www.airfrance.com"),
    "KL": ("KLM", "https://www.klm.com"),
    "EI": ("Aer Lingus", "https://www.aerlingus.com"),
    "TP": ("TAP Portugal", "https://www.flytap.com"),
    "TK": ("Turkish Airlines", "https://www.turkishairlines.com"),
    "EK": ("Emirates", "https://www.emirates.com"),
    "QR": ("Qatar Airways", "https://www.qatarairways.com"),
    "EY": ("Etihad", "https://www.etihad.com"),
    "JL": ("Japan Airlines", "https://www.jal.com"),
    "NH": ("ANA", "https://www.ana.co.jp"),
    "KE": ("Korean Air", "https://www.koreanair.com"),
    "OZ": ("Asiana", "https://flyasiana.com"),
    "CX": ("Cathay Pacific", "https://www.cathaypacific.com"),
    "SQ": ("Singapore Airlines", "https://www.singaporeair.com"),
    "MH": ("Malaysia Airlines", "https://www.malaysiaairlines.com"),
    "TG": ("Thai Airways", "https://www.thaiairways.com"),
    "VN": ("Vietnam Airlines", "https://www.vietnamairlines.com"),
    "PR": ("Philippine Airlines", "https://www.philippineairlines.com"),
    "BR": ("EVA Air", "https://www.evaair.com"),
    "CI": ("China Airlines", "https://www.china-airlines.com"),
}

LOGOS_FILE = DATA_DIR / "logos.json"
LOGO_URL = "https://pics.avs.io/120/120/{code}.png"  # Travelpayouts airline logo CDN
FARE_BASE = "https://www.aviasales.com"


def airline_info(code):
    name, url = AIRLINES.get(code, (code or "Various", None))
    return name, url


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

def fetch_logo_b64(code):
    """Fetch an airline logo from the Travelpayouts CDN as a data URI, or None."""
    import base64
    try:
        req = urllib.request.Request(LOGO_URL.format(code=code),
                                     headers={"User-Agent": "airfare-watch"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
        return "data:image/png;base64," + base64.b64encode(raw).decode()
    except Exception as e:  # noqa: BLE001
        print(f"  ! logo {code}: {e}", file=sys.stderr)
        return None


def update_logo_cache(codes):
    """Return {code: data_uri} for the given codes, fetching any not cached."""
    cache = load_json(LOGOS_FILE, {})
    changed = False
    for code in sorted(codes):
        if code and code not in cache:
            uri = fetch_logo_b64(code)
            if uri:
                cache[code] = uri
                changed = True
    if changed:
        save_json(LOGOS_FILE, cache)
    return {c: cache[c] for c in codes if c in cache}


def fetch_cheapest_fare(token, origin, destination, month, currency):
    """Return (price, airline_code, nonstop, depart_date, fare_link) or None.

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
    nonstop = best.get("transfers", 1) == 0
    depart = (best.get("departure_at") or month)[:10]
    link = best.get("link")
    fare_link = FARE_BASE + link if link else None
    return price, code, nonstop, depart, fare_link


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

SAMPLE_AIRLINES = ["UA", "AA", "DL", "NK", "F9", "BA"]

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
                None,
            )
        else:
            result = fetch_cheapest_fare(token, origin, dest, month, currency)

        if result is None:
            continue

        price, code, nonstop, depart_date, fare_link = result
        airline, airline_url = airline_info(code)
        past = history.get(key, [])
        flights.append({
            "route": key,
            "label": route.get("label", f"{origin} → {dest}"),
            "origin": origin,
            "destination": dest,
            "price": price,
            "currency": currency,
            "airline": airline,
            "airline_code": code,
            "airline_url": airline_url,
            "link": fare_link,
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

    logos = update_logo_cache({f["airline_code"] for f in flights if f["airline_code"]})

    save_json(PRICES_FILE, {
        "updated": now_iso,
        "departure_date": month,
        "currency": currency,
        "tiers": config.get("tiers", {}),
        "summary": summary,
        "logos": logos,
        "flights": sorted(flights, key=lambda f: f["price"]),
    })
    save_json(HISTORY_FILE, history)
    print(f"Wrote {len(flights)} routes to {PRICES_FILE} ({len(rated)} rated).")


if __name__ == "__main__":
    main()
