# Drivr limieten

Snelheidslimieten voor de Drivr-app, in kleine tegels. Wordt elke maand automatisch opnieuw gemaakt
door GitHub Actions uit OpenStreetMap (Nederland en België) en gepubliceerd via GitHub Pages.

- Tegels: `13/{x}/{y}.json` (web-mercator, zoom 13)
- Per land: `index/{land}.json` (lijst van tegels, voor offline downloaden in de app)
- `meta.json`: datum en aantallen

Data: © OpenStreetMap-bijdragers, beschikbaar onder de Open Database License (ODbL).

## Instellen (eenmalig)

1. Maak deze map een **openbare** GitHub-repository, bijvoorbeeld `drivr-limits`.
2. Settings → Pages → Source: **GitHub Actions**.
3. Actions → Limiettegels → **Run workflow**.
4. Na afloop staan de tegels op `https://<gebruikersnaam>.github.io/drivr-limits/`. Dat adres komt in de app
   (`SpeedLimitTileSource.baseURL`).
