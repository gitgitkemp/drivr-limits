#!/usr/bin/env python3
"""Maakt Drivr-limiettegels uit een OpenStreetMap-extract (.osm.pbf).

Gebruik:
    python3 make_tiles.py region parts/nl nl.pbf [meer.pbf ...]     # tegels van één regio
    python3 make_tiles.py merge site/ packs/ regions.json eu parts/  # regio's van een site samenvoegen
    python3 make_tiles.py out/ nl=netherlands.osm.pbf               # oud: alles in één keer
    python3 make_tiles.py --selftest                                # regels controleren

Uitvoer van merge:
    site/13/{x}/{y}.json     tegels in het formaat van docs/limietdata.md (regio's samengevoegd)
    site/index/{id}.json     welke tegels bij een regio horen (app 1.0 gebruikt dit voor offline)
    site/manifest.json       regio's van deze site: naam, groep, omtrek, grootte, downloadbestand
    site/meta.json           datum, aantallen, licentie
    packs/{id}.dpk           alle tegels van een regio in één bestand: regels "13/x/y<TAB>json",
                             samen gecomprimeerd met DEFLATE (raw, zonder kop; Apple: .zlib)
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
    "GB:nsl_single": 96.56, "GB:nsl_dual": 112.65, "GB:motorway": 112.65, "GB:nsl_restricted": 48.28,
    "IE:urban": 50, "IE:rural": 80, "IE:regional": 80, "IE:national": 100, "IE:motorway": 120,
    "ES:trunk": 100, "FR:trunk": 110,
}

# Punten weglaten die minder dan zoveel meter van de lijn afwijken (scheelt veel ruimte, niet zichtbaar).
SIMPLIFY_M = 2.0


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


def simplify(points, tol_m=SIMPLIFY_M):
    """Douglas-Peucker in meters (vlakke benadering, ruim goed genoeg op een paar kilometer)."""
    if len(points) < 3:
        return points
    k = math.cos(math.radians(points[0][0]))
    xy = [(lo * 111_320 * k, la * 110_574) for la, lo in points]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = xy[a], xy[b]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy)
        worst, idx = 0.0, -1
        for i in range(a + 1, b):
            px, py = xy[i]
            d = abs(dy * (px - ax) - dx * (py - ay)) / L if L > 0 else math.hypot(px - ax, py - ay)
            if d > worst:
                worst, idx = d, i
        if worst > tol_m:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k_ in zip(points, keep) if k_]


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


def handler(tiles, stats):
    """osmium-handler die wegen met een limiet per tegel verzamelt."""
    import osmium  # pas hier, zodat --selftest zonder pyosmium werkt

    class Handler(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.tiles = tiles

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
            for t, runs in split_per_tile(simplify(pts)).items():
                for run in runs:
                    # Als tekst bewaren: veel zuiniger met geheugen dan geneste lijsten.
                    self.tiles[t].add(json.dumps([round(limit), name, encode_points(run)],
                                                 ensure_ascii=False, separators=(",", ":")))

    return Handler()


def write_tiles(out, tiles):
    for (x, y), ways in tiles.items():
        d = os.path.join(out, str(ZOOM), str(x))
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{y}.json")
        if os.path.exists(path):   # al gevuld (ander bestand van dezelfde regio): samenvoegen
            with open(path, encoding="utf-8") as f:
                ways |= {json.dumps(w, ensure_ascii=False, separators=(",", ":")) for w in json.load(f)["w"]}
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"v":1,"w":[' + ",".join(sorted(ways)) + "]}")


def read_ways(path):
    with open(path, encoding="utf-8") as f:
        return {json.dumps(w, ensure_ascii=False, separators=(",", ":")) for w in json.load(f)["w"]}


def build_region(out, pbfs):
    """Tegels van één regio (één of meer kaarten) naar out/13/x/y.json."""
    stats = {"wegen": 0, "zonder_limiet": 0}
    for pbf in pbfs:
        tiles = defaultdict(set)
        handler(tiles, stats).apply_file(pbf, locations=True, idx="flex_mem")
        write_tiles(out, tiles)
        print(pbf, len(tiles), "tegels", stats, flush=True)
        del tiles
    with open(os.path.join(out, "stats.json"), "w") as f:
        json.dump(stats, f)


def lon_of(x, z=ZOOM):
    return x / (1 << z) * 360 - 180


def lat_of(y, z=ZOOM):
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / (1 << z)))))


def merge(site_dir, pack_dir, regions_path, site, parts_dir):
    """Voegt de regio's van één site samen, maakt per regio een downloadbestand en het manifest."""
    import zlib
    from datetime import date

    cfg = json.load(open(regions_path, encoding="utf-8"))
    owner, sites = cfg["owner"], cfg["sites"]
    mine = [r for r in cfg["regions"] if r["site"] == site]
    today = date.today().isoformat()

    # Welke regio's hebben welke tegel (alleen regio's die gelukt zijn)
    region_tiles, users = {}, defaultdict(list)
    for r in mine:
        root = os.path.join(parts_dir, r["id"], str(ZOOM))
        if not os.path.isdir(root):
            print("ontbreekt:", r["id"], flush=True)
            continue
        paths = []
        for x in os.listdir(root):
            for fn in os.listdir(os.path.join(root, x)):
                p = f"{ZOOM}/{x}/{fn[:-5]}"
                paths.append(p)
                users[p].append(r["id"])
        region_tiles[r["id"]] = sorted(paths)

    # Samenvoegen: een tegel op een grens krijgt de wegen van alle regio's
    for p, ids in users.items():
        dst = os.path.join(site_dir, p + ".json")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if len(ids) == 1:
            os.replace(os.path.join(parts_dir, ids[0], p + ".json"), dst)
        else:
            ways = set()
            for i in ids:
                ways |= read_ways(os.path.join(parts_dir, i, p + ".json"))
            with open(dst, "w", encoding="utf-8") as f:
                f.write('{"v":1,"w":[' + ",".join(sorted(ways)) + "]}")

    os.makedirs(os.path.join(site_dir, "index"), exist_ok=True)
    os.makedirs(pack_dir, exist_ok=True)
    listed = []
    for r in mine:
        paths = region_tiles.get(r["id"])
        if not paths:
            continue
        size = sum(os.path.getsize(os.path.join(site_dir, p + ".json")) for p in paths)
        with open(os.path.join(site_dir, "index", f"{r['id']}.json"), "w", encoding="utf-8") as f:
            json.dump({"land": r["id"], "datum": today, "bytes": size, "tiles": paths}, f, separators=(",", ":"))
        comp = zlib.compressobj(9, zlib.DEFLATED, -15)
        with open(os.path.join(pack_dir, f"{r['id']}.dpk"), "wb") as out:
            for p in paths:
                with open(os.path.join(site_dir, p + ".json"), "rb") as f:
                    out.write(comp.compress(p.encode() + b"\t" + f.read() + b"\n"))
            out.write(comp.flush())
        xs = [int(p.split("/")[1]) for p in paths]
        ys = [int(p.split("/")[2]) for p in paths]
        bbox = [round(lon_of(min(xs)), 4), round(lat_of(max(ys) + 1), 4),
                round(lon_of(max(xs) + 1), 4), round(lat_of(min(ys)), 4)]
        item = {"id": r["id"], "group": r["group"], "bbox": bbox, "tiles": len(paths), "bytes": size,
                "pack": os.path.getsize(os.path.join(pack_dir, f"{r['id']}.dpk"))}
        if r.get("name"):
            item["name"] = r["name"]
        listed.append(item)

    manifest = {
        "v": 2, "datum": today, "site": site,
        "tiles": f"https://{owner}.github.io/{sites[site]}/",
        "packs": f"https://github.com/{owner}/{sites[site]}/releases/download/data/",
        "sites": {k: f"https://{owner}.github.io/{v}/" for k, v in sites.items()},
        "regions": listed,
        "licentie": "© OpenStreetMap-bijdragers, ODbL",
    }
    with open(os.path.join(site_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, separators=(",", ":"))
    meta = {"zoom": ZOOM, "datum": today, "tegels": len(users),
            "regios": {i: len(p) for i, p in region_tiles.items()},
            "licentie": "© OpenStreetMap-bijdragers, ODbL"}
    with open(os.path.join(site_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print(json.dumps(meta, ensure_ascii=False))


def build(out, sources):
    """sources: lijst van (land, pad-naar-pbf). Land voor land, zodat het geheugen klein blijft."""
    from datetime import date

    stats = {"wegen": 0, "zonder_limiet": 0}
    country_tiles = defaultdict(set)

    for country, pbf in sources:
        tiles = defaultdict(set)
        handler(tiles, stats).apply_file(pbf, locations=True, idx="flex_mem")
        for (x, y), ways in tiles.items():
            d = os.path.join(out, str(ZOOM), str(x))
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, f"{y}.json")
            if os.path.exists(path):   # grensgebied: al gevuld door een ander land, samenvoegen
                with open(path, encoding="utf-8") as f:
                    ways |= {json.dumps(w, ensure_ascii=False, separators=(",", ":")) for w in json.load(f)["w"]}
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"v":1,"w":[' + ",".join(sorted(ways)) + "]}")
            country_tiles[country].add(f"{ZOOM}/{x}/{y}")
        print(country, len(tiles), "tegels", flush=True)
        del tiles

    os.makedirs(os.path.join(out, "index"), exist_ok=True)
    today = date.today().isoformat()
    total = set()
    for c, paths in country_tiles.items():
        paths = sorted(paths)
        total |= set(paths)
        size = sum(os.path.getsize(os.path.join(out, p + ".json")) for p in paths)
        with open(os.path.join(out, "index", f"{c}.json"), "w", encoding="utf-8") as f:
            json.dump({"land": c, "datum": today, "bytes": size, "tiles": paths}, f, separators=(",", ":"))
    meta = {"zoom": ZOOM, "datum": today, "tegels": len(total), **stats,
            "landen": {c: len(p) for c, p in country_tiles.items()},
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
    # Vereenvoudigen: rechte lijn met kleine ruis wordt twee punten, een echte bocht blijft
    straight = [(52.0 + i * 0.0001, 4.0 + (0.000005 if i % 2 else 0)) for i in range(20)]
    assert len(simplify(straight)) == 2, simplify(straight)
    corner = [(52.0, 4.0), (52.001, 4.0), (52.001, 4.0015)]
    assert simplify(corner) == corner
    assert parse("IE:national") == 100
    print("selftest ok")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--selftest":
        selftest()
    elif len(sys.argv) >= 4 and sys.argv[1] == "region":
        build_region(sys.argv[2], sys.argv[3:])
    elif len(sys.argv) == 7 and sys.argv[1] == "merge":
        merge(*sys.argv[2:7])
    elif len(sys.argv) >= 3 and all("=" in a for a in sys.argv[2:]):
        build(sys.argv[1], [tuple(a.split("=", 1)) for a in sys.argv[2:]])
    else:
        print(__doc__)
        sys.exit(1)
