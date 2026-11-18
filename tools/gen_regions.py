#!/usr/bin/env python3
"""Maakt regions.json: welke regio's er zijn, uit welke Geofabrik-kaarten, en op welke site ze staan.

Eenmalig of bij nieuwe regio's draaien (niet in de maandelijkse taak):
    curl -o geo.json https://download.geofabrik.de/index-v1-nogeom.json
    # sizes.txt: regels "<geofabrik-id> <bytes>" (grootte van de .osm.pbf, voor de verdeling over sites)
    python3 tools/gen_regions.py geo.json sizes.txt > regions.json

GitHub Pages staat per site maximaal 1 GB toe. Daarom staan de regio's verdeeld over meerdere
repo's (sites). Elke repo bouwt alleen zijn eigen regio's.
"""
import json
import sys

# Sites: repo-naam per site. De eerste is de oorspronkelijke (daar leest app 1.0 nog uit).
OWNER = "gitgitkemp"
SITES = {"eu": "drivr-limits", "s2": "drivr-limits-2"}
# Geschatte tegelgrootte als deel van de .osm.pbf (gemeten op NL/BE/DE/FR/ES: 0,021 tot 0,036)
RATIO = 0.034
BUDGET = 800e6          # ruim onder 1 GB, zodat er groei in past

# Al online in app 1.0: moeten op de eerste site blijven, met dezelfde codes.
LEGACY = {"nl": ["europe/netherlands"], "be": ["europe/belgium"], "de": ["europe/germany"],
          "fr": ["europe/france"], "es": ["europe/spain"],
          "uk": ["europe/great-britain", "europe/ireland-and-northern-ireland"]}

# Landcode per Geofabrik-naam (ISO 3166-1 waar die bestaat)
ISO = {
    "albania": "al", "andorra": "ad", "austria": "at", "azores": "pt-20", "belarus": "by", "belgium": "be",
    "bosnia-herzegovina": "ba", "bulgaria": "bg", "croatia": "hr", "cyprus": "cy", "czech-republic": "cz",
    "denmark": "dk", "estonia": "ee", "faroe-islands": "fo", "finland": "fi", "france": "fr", "georgia": "ge",
    "germany": "de", "greece": "gr", "guernsey-jersey": "gg", "hungary": "hu", "iceland": "is",
    "isle-of-man": "im", "italy": "it", "kosovo": "xk", "latvia": "lv", "liechtenstein": "li",
    "lithuania": "lt", "luxembourg": "lu", "macedonia": "mk", "malta": "mt", "moldova": "md", "monaco": "mc",
    "montenegro": "me", "netherlands": "nl", "norway": "no", "poland": "pl", "portugal": "pt",
    "romania": "ro", "serbia": "rs", "slovakia": "sk", "slovenia": "si", "spain": "es", "sweden": "se",
    "switzerland": "ch", "turkey": "tr", "ukraine": "ua",
}
SKIP = {"alps", "dach", "britain-and-ireland", "great-britain", "ireland-and-northern-ireland", "united-kingdom"}
STATES = {  # postcode-afkortingen
    "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar", "california": "ca", "colorado": "co",
    "connecticut": "ct", "delaware": "de", "district-of-columbia": "dc", "florida": "fl", "georgia": "ga",
    "hawaii": "hi", "idaho": "id", "illinois": "il", "indiana": "in", "iowa": "ia", "kansas": "ks",
    "kentucky": "ky", "louisiana": "la", "maine": "me", "maryland": "md", "massachusetts": "ma",
    "michigan": "mi", "minnesota": "mn", "mississippi": "ms", "missouri": "mo", "montana": "mt",
    "nebraska": "ne", "nevada": "nv", "new-hampshire": "nh", "new-jersey": "nj", "new-mexico": "nm",
    "new-york": "ny", "north-carolina": "nc", "north-dakota": "nd", "ohio": "oh", "oklahoma": "ok",
    "oregon": "or", "pennsylvania": "pa", "puerto-rico": "pr", "rhode-island": "ri", "south-carolina": "sc",
    "south-dakota": "sd", "tennessee": "tn", "texas": "tx", "us-virgin-islands": "vi", "utah": "ut",
    "vermont": "vt", "virginia": "va", "washington": "wa", "west-virginia": "wv", "wisconsin": "wi",
    "wyoming": "wy",
}
STATE_NAMES = {"district-of-columbia": "District of Columbia", "us-virgin-islands": "U.S. Virgin Islands"}


def main(geo_path, sizes_path):
    geo = {f["properties"]["id"]: f["properties"] for f in json.load(open(geo_path))["features"]}
    sizes = {}
    for line in open(sizes_path):
        k, _, v = line.strip().partition(" ")
        sizes[k] = int(v or 0)

    regions = []
    for code, paths in LEGACY.items():
        est = sum(sizes.get(p.split("/")[1], 0) for p in paths)
        if code == "uk":
            est = sizes.get("united-kingdom", 0)
        regions.append({"id": code, "group": "europe", "pbf": paths, "est": est})
    for gid, p in geo.items():
        if p.get("parent") == "europe" and gid not in SKIP and ISO.get(gid) not in LEGACY:
            regions.append({"id": ISO[gid], "group": "europe", "name": p["name"], "pbf": [f"europe/{gid}"],
                            "est": sizes.get(gid, 0)})
    for gid, p in geo.items():
        if gid.startswith("us/"):
            s = gid[3:]
            regions.append({"id": "us-" + STATES[s], "group": "us",
                            "name": STATE_NAMES.get(s, s.replace("-", " ").title()),
                            "pbf": [f"north-america/us/{s}"], "est": sizes.get(gid, 0)})

    # Verdelen: eerst de oude zes op site 1, dan de grootste regio's waar het nog past
    load = {s: 0.0 for s in SITES}
    for r in regions:
        if r["id"] in LEGACY:
            r["site"] = "eu"
            load["eu"] += r["est"] * RATIO
    for r in sorted((r for r in regions if "site" not in r), key=lambda r: -r["est"]):
        site = "eu" if r["group"] == "europe" and load["eu"] + r["est"] * RATIO < BUDGET else "s2"
        r["site"] = site
        load[site] += r["est"] * RATIO
    for r in regions:
        r["est"] = round(r["est"] * RATIO / 1e6, 1)     # geschatte MB tegels
    out = {"owner": OWNER, "sites": SITES, "regions": sorted(regions, key=lambda r: (r["group"], r["id"]))}
    print(json.dumps(out, indent=1, ensure_ascii=False))
    print({s: round(v / 1e6) for s, v in load.items()}, "MB geschat", file=sys.stderr)


if __name__ == "__main__":
    main(*sys.argv[1:3])
