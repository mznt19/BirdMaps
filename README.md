# BirdMaps Ottawa — rebooted v1.0

Independent prototype for finding scooter parking and avoiding **published Bird no-ride zones** in Ottawa.

**Live Render site:** https://birdmaps-ottawa.onrender.com

## How it works

- Map and address search are independent of Bird data. The Leaflet/OpenStreetMap map appears immediately; address suggestions use `/api/search` (Photon then Nominatim) and can fall back to Photon directly in the browser.
- `/api/data` attempts Bird's publicly advertised GBFS feeds and the City of Ottawa E-Scooter ArcGIS service separately. The backend returns actual named sources, parking locations, zones, and diagnostic warnings.
- Only Bird's **real no-ride polygons** permit the route engine to run. When no Bird geofences or approved parking locations are available, the app explicitly refuses to assert a permitted scooter route.
- `/api/route` calls a local street-graph pathfinder that blocks road edges intersecting fetched no-ride polygons. This is a research prototype, **not a Bird-certified navigation system**.
- Ottawa's rules also exclude sidewalks and NCC pathways, which are not fully encoded in this prototype. The official Bird app remains authoritative.
- No fake docks or sample geofences are supplied.

## Troubleshooting

- `/api/health` should return `{"ok":true,"version":"1.0-rebuild"}`.
- `/api/data` reports which feeds responded and why any failed.
- Render cold starts can delay first requests; if *all* APIs fail, check the deployment logs and the live URL first.
- The official Ottawa e-scooter GIS source is `https://services.arcgis.com/G6F8XLCl5KtAlZ2G/arcgis/rest/services/E_Scooter/FeatureServer`.
- The Bird Ottawa discovery URL is `https://mds.bird.co/gbfs/v2/public/ottawa/gbfs.json`.

## Deployment

This repository includes `render.yaml`. On Render, create a Python web service or Blueprint connected to this repository. Start command: `python server.py`. The app listens on the `PORT` provided by the host and uses only the Python standard library.

## Version history

The original patched v0.6 application is preserved on the Git branch `archive/v0-6-before-rebuild`.

## Verification

GitHub Actions check Python compilation, browser JavaScript syntax, the API health endpoint, and a synthetic no-ride polygon test on every push. **Live Bird data access is not guaranteed and must be tested from the deployed host.**
