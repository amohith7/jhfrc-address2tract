"""
Build a file of ~1000 REAL, geocodable addresses from OpenStreetMap via the
Overpass API (free, no key). Pulls addressed nodes (house number + street +
city + ZIP) across several Tennessee cities so the sample reflects real intake
data. Writes data/input/real_addresses_1000.csv with columns:
    client_id, street, city, state, zip
"""

from __future__ import annotations

import time
import requests
import pandas as pd

OVERPASS_ENDPOINTS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]
HEADERS = {
    "User-Agent": "JHFRC-address2tract-test/1.0 (research; contact mohith-addepalli@utc.edu)"
}

# (name, south, west, north, east) bounding boxes over TN city cores
AREAS = [
    ("Chattanooga", 35.00, -85.35, 35.12, -85.18),
    ("Nashville", 36.10, -86.86, 36.22, -86.68),
    ("Knoxville", 35.93, -84.04, 36.03, -83.87),
    ("Memphis", 35.10, -90.09, 35.20, -89.92),
    ("Clarksville", 36.50, -87.42, 36.58, -87.30),
    ("Murfreesboro", 35.82, -86.44, 35.90, -86.34),
]

PER_AREA = 300  # request cap per area; we trim to 1000 total afterward


def fetch_area(name, s, w, n, e):
    q = (
        f"[out:json][timeout:90];"
        f'(node["addr:housenumber"]["addr:street"]["addr:postcode"]'
        f"({s},{w},{n},{e}););"
        f"out body {PER_AREA};"
    )
    for attempt in range(3):
        for ep in OVERPASS_ENDPOINTS:
            try:
                r = requests.post(ep, data={"data": q}, headers=HEADERS, timeout=120)
                r.raise_for_status()
                elems = r.json().get("elements", [])
                print(f"  {name}: {len(elems)} address points ({ep.split('/')[2]})")
                return elems
            except Exception as ex:
                print(f"  {name}: {ep.split('/')[2]} failed ({ex})")
        time.sleep(5)
    print(f"  {name}: giving up")
    return []


rows = []
seen = set()
for area in AREAS:
    name = area[0]
    for el in fetch_area(*area):
        t = el.get("tags", {})
        street = t.get("addr:street", "").strip()
        hn = t.get("addr:housenumber", "").strip()
        city = t.get("addr:city", "").strip() or name
        state = t.get("addr:state", "").strip() or "TN"
        zc = t.get("addr:postcode", "").strip()[:5]
        if not (hn and street and zc):
            continue
        full_street = f"{hn} {street}"
        key = (full_street.lower(), zc)
        if key in seen:
            continue
        seen.add(key)
        rows.append((full_street, city, state, zc))
    time.sleep(2)  # be polite to Overpass between areas

df = pd.DataFrame(rows, columns=["street", "city", "state", "zip"])
df = df.head(1000).reset_index(drop=True)
df.insert(0, "client_id", [f"C{i + 1:04d}" for i in range(len(df))])

out = "data/input/real_addresses_1000.csv"
df.to_csv(out, index=False)
print(f"\nWrote {len(df)} real addresses to {out}")
print(df.head(8).to_string(index=False))
