#!/usr/bin/env python3
"""Maakt Drivr-limiettegels uit een OpenStreetMap-extract (.osm.pbf).

Gebruik:
    python3 make_tiles.py out/ nl=netherlands.osm.pbf be=belgium.osm.pbf   # tegels maken
    python3 make_tiles.py --selftest                                       # regels controleren

Uitvoer:
    out/13/{x}/{y}.json      tegels in het formaat van docs/limietdata.md (landen samengevoegd)
    out/index/{land}.json    welke tegels bij een land horen, voor de offline download in de app
    out/meta.json            datum, aantallen, licentie
Draait normaal in GitHub Actions (.github/workflows/limits.yml), niet op je eigen computer.
Vereist: pip install osmium   (pyosmium)
Data: © OpenStreetMap-bijdragers, ODbL.
"""
import json
import math
import os
import re
import sys
from collections import defaultdict

ZOOM = 13
SCALE = 100_000

# Wegen waar je met auto of motor rijdt
ROADS = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
    "living_street", "service", "road",
    "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
}

# Zelfde zones als SpeedLimit.zones in de app. None = geen limiet.
ZONES = {
    "NL:urban": 50, "NL:rural": 80, "NL:motorway": 130, "NL:trunk": 100, "NL:zone30": 30, "NL:zone60": 60,
    "BE:urban": 50, "BE-VLG:rural": 70, "BE-WAL:rural": 90, "BE:rural": 90, "BE:motorway": 120, "BE:zone30": 30,
    "DE:urban": 50, "DE:rural": 100, "DE:motorway": None, "DE:zone30": 30, "DE:living_street": 7,
    "FR:urban": 50, "FR:rural": 80, "FR:motorway": 130, "FR:zone30": 30,
    "AT:urban": 50, "AT:rural": 100, "AT:motorway": 130,
    "CH:urban": 50, "CH:rural": 80, "CH:motorway": 120,
    "LU:urban": 50, "LU:rural": 90, "LU:motorway": 130,
    "DK:urban": 50, "DK:rural": 80, "DK:motorway": 130,
    "IT:urban": 50, "IT:rural": 90, "IT:motorway": 130,
    "ES:urban": 50, "ES:rural": 90, "ES:motorway": 120,
    "GB:nsl_single": 96.56, "GB:nsl_dual": 112.65, "GB:motorway": 112.65,
}


def parse(raw):
    """Zelfde regels als SpeedLimit.parse in de app. Geeft km/u of None."""
    value = (raw or "").split(";")[0].strip()
    if not value:
        return None
    low = value.lower()
    if low in ("none", "signals", "variable", "unknown", "implicit"):
        return None
    if low == "walk":
        return 7
    if value in ZONES:
        return ZONES[value]
    if ":" in value:
        m = re.search(r"zone(\d+)", low)
        if m:
            return float(m.group(1))
        if low.endswith(":urban"):
            return 50
        return None
    mph = "mph" in low
    digits = "".join(c for c in value if c.isdigit() or c == ".")
    try:
        n = float(digits)
    except ValueError:
        return None
    if not 0 < n < 200:
        return None
    return n * 1.609344 if mph else n


def limit_from_tags(tags):
    for key in ("maxspeed", "maxspeed:type", "source:maxspeed", "zone:maxspeed"):
        if key in tags:
            return parse(tags[key])
    return None


def tile_of(lat, lon, z=ZOOM):
    n = 1 << z
    x = int(math.floor((lon + 180) / 360 * n))
    y = int(math.floor((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n))
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def encode_points(points):
    lat, lon = round(points[0][0] * SCALE), round(points[0][1] * SCALE)
    raw = [lat, lon]
    for la_, lo_ in points[1:]:
        la, lo = round(la_ * SCALE), round(lo_ * SCALE)
        raw += [la - lat, lo - lon]
        lat, lon = la, lo
    return raw


def split_per_tile(points):
    """Knip een weg in stukken per tegel. Een stuk dat over een tegelgrens gaat, komt in beide tegels."""
    pieces = defaultdict(list)          # tegel -> lijst van polylijnen
    for a, b in zip(points, points[1:]):
        for t in {tile_of(*a), tile_of(*b)}:
            runs = pieces[t]
            if runs and runs[-1][-1] == a:
                runs[-1].append(b)
            else:
                runs.append([a, b])
    return pieces


def build(out, sources):
    """sources: lijst van (land, pad-naar-pbf)."""
    import osmium  # pas hier, zodat --selftest zonder pyosmium werkt
    from datetime import date

    tiles = defaultdict(list)
    tile_countries = defaultdict(set)
    stats = {"wegen": 0, "zonder_limiet": 0}

    class Handler(osmium.SimpleHandler):
        def __init__(self, country):
            super().__init__()
            self.country = country

        def way(self, w):
            tags = w.tags
            if tags.get("highway") not in ROADS:
                return
            limit = limit_from_tags(tags)
            if limit is None:
                stats["zonder_limiet"] += 1
                return
            try:
                pts = [(n.location.lat, n.location.lon) for n in w.nodes if n.location.valid()]
            except osmium.InvalidLocationError:
                return
            if len(pts) < 2:
                return
            stats["wegen"] += 1
            name = tags.get("name") or tags.get("ref") or ""
            for t, runs in split_per_tile(pts).items():
                tile_countries[t].add(self.country)
                for run in runs:
                    tiles[t].append([round(limit), name, encode_points(run)])

    for country, pbf in sources:
        # Grensgebieden zitten in beide extracten: dubbele wegen per tegel wegfilteren gebeurt hieronder.
        Handler(country).apply_file(pbf, locations=True, idx="flex_mem")

    per_country = defaultdict(lambda: {"tiles": [], "bytes": 0})
    for (x, y), ways in tiles.items():
        unique = list({json.dumps(w, separators=(",", ":")): w for w in ways}.values())
        d = os.path.join(out, str(ZOOM), str(x))
        os.makedirs(d, exist_ok=True)
        body = json.dumps({"v": 1, "w": unique}, ensure_ascii=False, separators=(",", ":"))
        with open(os.path.join(d, f"{y}.json"), "w", encoding="utf-8") as f:
            f.write(body)
        for c in tile_countries[(x, y)]:
            per_country[c]["tiles"].append(f"{ZOOM}/{x}/{y}")
            per_country[c]["bytes"] += len(body.encode("utf-8"))

    os.makedirs(os.path.join(out, "index"), exist_ok=True)
    today = date.today().isoformat()
    for c, info in per_country.items():
        info["tiles"].sort()
        with open(os.path.join(out, "index", f"{c}.json"), "w", encoding="utf-8") as f:
            json.dump({"land": c, "datum": today, "bytes": info["bytes"], "tiles": info["tiles"]}, f, separators=(",", ":"))
    meta = {"zoom": ZOOM, "datum": today, "tegels": len(tiles), **stats,
            "landen": {c: len(i["tiles"]) for c, i in per_country.items()},
            "licentie": "© OpenStreetMap-bijdragers, ODbL"}
    with open(os.path.join(out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print(json.dumps(meta, ensure_ascii=False))


def selftest():
    assert parse("50") == 50
    assert parse("100;80") == 100
    assert abs(parse("30 mph") - 48.28) < 0.01
    assert parse("NL:urban") == 50
    assert parse("NL:rural") == 80
    assert parse("DE:motorway") is None
    assert parse("none") is None and parse("signals") is None
    assert parse("walk") == 7
    assert parse("PL:zone30") == 30
    assert parse("") is None
    assert limit_from_tags({"source:maxspeed": "NL:urban"}) == 50
    assert tile_of(52.3731, 4.8922) == (4207, 2692)      # zelfde als de Swift-test
    raw = encode_points([(52.35746, 4.91011), (52.35700, 4.91039)])
    assert raw == [5235746, 491011, -46, 28], raw
    # Weg over een tegelgrens: in beide tegels
    x, y = 4207, 2692
    east = (x + 1) / 8192 * 360 - 180
    pieces = split_per_tile([(52.3731, east - 0.001), (52.3731, east + 0.001)])
    assert set(pieces) == {(x, y), (x + 1, y)}, pieces
    print("selftest ok")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--selftest":
        selftest()
    elif len(sys.argv) >= 3 and all("=" in a for a in sys.argv[2:]):
        build(sys.argv[1], [tuple(a.split("=", 1)) for a in sys.argv[2:]])
    else:
        print(__doc__)
        sys.exit(1)
