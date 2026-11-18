# Drivr limieten

Snelheidslimieten voor de Drivr-app, in kleine tegels. Wordt elke maand automatisch opnieuw gemaakt
door GitHub Actions uit OpenStreetMap en gepubliceerd via GitHub Pages.

GitHub Pages staat maximaal 1 GB per site toe. Daarom zijn de regio's verdeeld over meerdere repo's
(`regions.json`, veld `site`): `drivr-limits` en `drivr-limits-2`. Beide repo's hebben dezelfde bestanden;
elke repo bouwt alleen zijn eigen regio's.

- Tegels: `13/{x}/{y}.json` (web-mercator, zoom 13)
- `manifest.json`: regio's van deze site (naam, groep, omtrek, grootte) en de adressen van alle sites
- Per regio: `index/{id}.json` (lijst van tegels, voor app 1.0)
- Downloadbestanden per regio: release `data`, bestand `{id}.dpk` (alle tegels, DEFLATE-gecomprimeerd)
- `meta.json`: datum en aantallen

Data: © OpenStreetMap-bijdragers, beschikbaar onder de Open Database License (ODbL).

## Werking

1. `plan`: kiest de regio's van deze repo uit `regions.json`.
2. `region` (per regio, parallel): kaart van Geofabrik downloaden, voorfilteren, tegels maken.
3. `merge`: tegels samenvoegen (grenstegels krijgen de wegen van beide kanten), manifest en downloadbestanden.
4. `deploy`: naar GitHub Pages.

Proefrun zonder publiceren: Actions → Limiettegels → Run workflow, bij "only" bijvoorbeeld `lu,mc`.

Nieuwe regio's: `tools/gen_regions.py` (zie de uitleg bovenin) en `regions.json` in beide repo's bijwerken.
