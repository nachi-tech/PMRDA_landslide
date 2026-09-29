# PMRDA Landslide Screening Dashboard

Leaflet/Vite dashboard for inspecting the existing PMRDA landslide-screening outputs at slope-unit level.

## Scientific/interpretation constraints

This app visualizes existing analytical outputs. It does **not** recompute candidate source zones, runout paths, envelopes, metric fields or building exposure in the browser.

Use the dashboard wording carefully:

- susceptibility is not probability;
- candidate source zones are not the full slope unit;
- runout envelopes are regional screening envelopes, not exact impact footprints;
- animation is a progressive reveal of stored path geometry, not velocity/travel-time simulation;
- building exposure means footprint intersection with screening geometry, not damage or risk;
- metric attributes are from the EPSG:32643 analytical workflow and should not be recalculated from EPSG:4326 display geometry.

## Prepare data

From the repository root:

```bash
python pmrda_prepare_leaflet_dashboard.py --sample-size 50
```

For full export:

```bash
python pmrda_prepare_leaflet_dashboard.py
```

The script writes dashboard-ready files to:

```text
dashboard/public/data/
├── slope_units.geojson
├── source_zones.geojson
├── runout_paths.geojson
├── runout_envelopes.geojson
├── exposed_buildings.geojson
├── ridges.geojson
├── slope_summary.json
└── model_metadata.json
```

The script requires GeoPandas/Fiona or Pyogrio in the active Python/GIS environment.

## Run the dashboard

From `dashboard/`:

```bash
npm install
npm run dev
```

Open the Vite URL, usually:

```text
http://localhost:5173
```

## Build

```bash
npm run build
```

## First milestone workflow

1. Open dashboard.
2. Click a slope unit.
3. Inspect slope and susceptibility metrics.
4. Inspect candidate source zone(s).
5. Click **Animate runout**.
6. Watch stored runout trajectories progressively reveal.
7. Inspect the existing runout envelope and exposed buildings.
8. Toggle stakeholder/technical mode for path diagnostics.
