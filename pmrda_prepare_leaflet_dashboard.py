# -*- coding: utf-8 -*-
"""
Prepare PMRDA landslide-screening outputs for the Leaflet dashboard.

This script starts AFTER the authoritative QGIS/Python analytical pipeline has
created slope units, candidate source zones, runout paths, runout envelopes and
building exposure layers. It does not recompute source zones, runout, exposure,
areas or distances. Its job is to validate lineage and export display copies in
EPSG:4326 for Leaflet.

Default inputs:
    data/slope_units_master.gpkg        layer slope_units_master
    data/candidate_source_zones.gpkg    layer candidate_source_zones
    data/runout_paths.gpkg              layer runout_paths
    data/runout_envelopes.gpkg          layer runout_envelopes
    data/building_exposure.gpkg         layer building_exposure, optional
    data/ridges_cleaned.gpkg            optional context layer

Default outputs:
    dashboard/public/data/slope_units.geojson
    dashboard/public/data/source_zones.geojson
    dashboard/public/data/runout_paths.geojson
    dashboard/public/data/runout_envelopes.geojson
    dashboard/public/data/exposed_buildings.geojson
    dashboard/public/data/ridges.geojson
    dashboard/public/data/slope_summary.json
    dashboard/public/data/model_metadata.json

Examples:
    python pmrda_prepare_leaflet_dashboard.py
    python pmrda_prepare_leaflet_dashboard.py --sample-size 50
    python pmrda_prepare_leaflet_dashboard.py --su-ids 1 2 3 4
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

try:
    import geopandas as gpd
except ImportError as exc:  # pragma: no cover - runtime environment guard
    raise SystemExit(
        "GeoPandas is required to export dashboard data. Install it in a GIS "
        "Python environment, for example: pip install geopandas pyogrio fiona"
    ) from exc

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "dashboard" / "public" / "data"

ANALYSIS_CRS = "EPSG:32643"
DISPLAY_CRS = "EPSG:4326"

INPUTS = {
    "slope_units": {
        "path": DATA_DIR / "slope_units_master.gpkg",
        "layer": "slope_units_master",
        "output": "slope_units.geojson",
        "required": True,
        "fields": [
            "SU_ID", "AREA_HA", "S_MEAN", "S_P75", "S_P90", "S_MAX",
            "ASP_MEAN", "ASP_CONC", "Z_MIN", "Z_MAX", "RELIEF",
            "CURV_MEAN", "MEAN_LSI", "PCT_C1", "PCT_C2", "PCT_C3",
            "PCT_C4", "PCT_C5", "HIGH_PCT", "DOM_CLASS",
            "N_BLD_SRC", "N_BLD_RUN", "N_BLD_BOTH", "N_BLD_TOT",
            "BLD_FP_M2", "SRC_OV_M2", "RUN_OV_M2", "TOT_OV_M2",
            "MEAN_OV_P", "MAX_OV_P", "MAX_RUN_M", "MAX_DROP_M",
        ],
        "required_fields": ["SU_ID"],
    },
    "source_zones": {
        "path": DATA_DIR / "candidate_source_zones.gpkg",
        "layer": "candidate_source_zones",
        "output": "source_zones.geojson",
        "required": True,
        "fields": [
            "SOURCE_ID", "SU_ID", "AREA_M2", "AREA_HA", "S_MEAN", "S_P90",
            "LSI_MEAN", "Z_MIN", "Z_MAX", "Z_MEAN", "RELIEF",
            "ASP_MEAN", "ASP_CONC", "REL_POS",
        ],
        "required_fields": ["SOURCE_ID", "SU_ID"],
    },
    "runout_paths": {
        "path": DATA_DIR / "runout_paths.gpkg",
        "layer": "runout_paths",
        "output": "runout_paths.geojson",
        "required": True,
        "fields": [
            "PATH_ID", "SOURCE_ID", "SU_ID", "START_Z", "END_Z", "DROP_M",
            "LENGTH_M", "REACH_DEG", "N_STEPS", "END_REASON",
        ],
        "required_fields": ["PATH_ID", "SOURCE_ID", "SU_ID"],
    },
    "runout_envelopes": {
        "path": DATA_DIR / "runout_envelopes.gpkg",
        "layer": "runout_envelopes",
        "output": "runout_envelopes.geojson",
        "required": True,
        "fields": ["SOURCE_ID", "SU_ID", "N_PATHS", "MAX_LEN_M", "MAX_DROP_M", "AREA_HA"],
        "required_fields": ["SOURCE_ID", "SU_ID"],
    },
    "exposed_buildings": {
        "path": DATA_DIR / "building_exposure.gpkg",
        "layer": "building_exposure",
        "output": "exposed_buildings.geojson",
        "required": False,
        "fields": [
            "BUILD_FID", "SU_ID", "EXP_TYPE", "BLDG_AREA", "SRC_OVLP",
            "RUN_OVLP", "TOT_OVLP", "OVLP_PCT",
        ],
        "required_fields": ["BUILD_FID", "SU_ID", "EXP_TYPE"],
    },
    "ridges": {
        "path": DATA_DIR / "ridges_cleaned.gpkg",
        "layer": None,
        "output": "ridges.geojson",
        "required": False,
        "fields": [],
        "required_fields": [],
    },
}

EXPOSURE_SUMMARY_PATH = DATA_DIR / "slope_exposure_summary.gpkg"
EXPOSURE_SUMMARY_LAYER = "slope_exposure_summary"
EXPOSURE_SUMMARY_FIELDS = [
    "SU_ID", "N_BLD_SRC", "N_BLD_RUN", "N_BLD_BOTH", "N_BLD_TOT",
    "BLD_FP_M2", "SRC_OV_M2", "RUN_OV_M2", "TOT_OV_M2",
    "MEAN_OV_P", "MAX_OV_P", "MAX_RUN_M", "MAX_DROP_M",
]


def _normalise_id_series(series):
    return series.astype(str)


def read_layer(name: str):
    spec = INPUTS[name]
    path = spec["path"]
    if not path.exists():
        if spec["required"]:
            raise FileNotFoundError(f"Required input not found: {path}")
        return None

    kwargs = {"layer": spec["layer"]} if spec["layer"] else {}
    try:
        gdf = gpd.read_file(path, **kwargs)
    except Exception as exc:
        if spec["required"]:
            raise RuntimeError(f"Could not read {name} from {path}: {exc}") from exc
        print(f"[WARN] Optional layer {name} could not be read from {path}: {exc}")
        return None

    if gdf.empty:
        print(f"[WARN] {name} is empty: {path}")

    missing = [field for field in spec["required_fields"] if field not in gdf.columns]
    if missing:
        raise ValueError(f"{name} is missing required field(s): {', '.join(missing)}")

    return gdf


def keep_fields(gdf, fields: list[str]):
    available = [field for field in fields if field in gdf.columns]
    columns = available + [gdf.geometry.name]
    return gdf.loc[:, columns].copy()


def ensure_display_crs(gdf, name: str):
    if gdf.crs is None:
        print(f"[WARN] {name} has no CRS. Assuming {ANALYSIS_CRS} before display export.")
        gdf = gdf.set_crs(ANALYSIS_CRS)
    return gdf.to_crs(DISPLAY_CRS)


def write_geojson(gdf, output_name: str):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUT_DIR / output_name
    gdf.to_file(output_path, driver="GeoJSON")
    print(f"[OK] Wrote {len(gdf):,} features -> {output_path}")


def validate_unique(gdf, field: str, label: str):
    if field not in gdf.columns:
        return
    duplicates = gdf[field][gdf[field].duplicated()].unique()
    if len(duplicates):
        sample = ", ".join(map(str, duplicates[:10]))
        raise ValueError(f"{label}.{field} must be unique; duplicate sample: {sample}")


def validate_subset(child, child_field: str, parent_values: set[str], label: str):
    if child is None or child_field not in child.columns:
        return
    child_values = set(_normalise_id_series(child[child_field].dropna()))
    missing = child_values - parent_values
    if missing:
        sample = ", ".join(sorted(missing)[:10])
        raise ValueError(f"{label}.{child_field} contains IDs not present upstream. Sample: {sample}")


def merge_exposure_summary(slopes):
    """Merge optional per-slope exposure metrics onto slope_units by SU_ID.

    The geometry remains the slope_units_master geometry. Only summary attributes
    are joined. Existing columns on slope_units_master are preserved unless the
    summary provides a missing exposure field.
    """
    if not EXPOSURE_SUMMARY_PATH.exists():
        return slopes

    try:
        summary = gpd.read_file(EXPOSURE_SUMMARY_PATH, layer=EXPOSURE_SUMMARY_LAYER)
    except Exception as exc:
        print(f"[WARN] Could not read optional slope exposure summary: {exc}")
        return slopes

    if "SU_ID" not in summary.columns:
        print("[WARN] slope_exposure_summary exists but has no SU_ID; skipping merge.")
        return slopes

    fields = [field for field in EXPOSURE_SUMMARY_FIELDS if field in summary.columns]
    summary_attrs = summary.loc[:, fields].copy()
    merged = slopes.copy()
    merged["_SU_ID_JOIN"] = _normalise_id_series(merged["SU_ID"])
    summary_attrs["_SU_ID_JOIN"] = _normalise_id_series(summary_attrs["SU_ID"])

    for field in fields:
        if field != "SU_ID" and field in merged.columns:
            summary_attrs = summary_attrs.drop(columns=[field])

    summary_attrs = summary_attrs.drop(columns=["SU_ID"], errors="ignore")
    merged = merged.merge(summary_attrs, on="_SU_ID_JOIN", how="left")
    merged = merged.drop(columns=["_SU_ID_JOIN"])
    print("[OK] Merged optional slope_exposure_summary attributes onto slope units.")
    return merged


def filter_by_su(gdf, su_ids: set[str] | None):
    if gdf is None or su_ids is None or "SU_ID" not in gdf.columns:
        return gdf
    return gdf[_normalise_id_series(gdf["SU_ID"]).isin(su_ids)].copy()


def choose_sample_su_ids(slopes, sources, sample_size: int | None) -> set[str] | None:
    if not sample_size:
        return None

    source_su = set(_normalise_id_series(sources["SU_ID"].dropna())) if sources is not None else set()
    slope_ids = list(_normalise_id_series(slopes["SU_ID"]))
    with_sources = [su for su in slope_ids if su in source_su]
    without_sources = [su for su in slope_ids if su not in source_su]

    selected: list[str] = []
    selected.extend(with_sources[: max(1, sample_size // 2)])
    selected.extend(without_sources[: max(0, sample_size - len(selected))])

    if len(selected) < sample_size:
        for su in slope_ids:
            if su not in selected:
                selected.append(su)
            if len(selected) >= sample_size:
                break

    print(f"[INFO] Sample export selected {len(selected):,} slope units.")
    return set(selected)


def build_slope_summary(slopes, sources, paths, envelopes, buildings):
    def count_by_su(gdf):
        if gdf is None or "SU_ID" not in gdf.columns:
            return {}
        return _normalise_id_series(gdf["SU_ID"]).value_counts().to_dict()

    source_counts = count_by_su(sources)
    path_counts = count_by_su(paths)
    envelope_counts = count_by_su(envelopes)
    building_counts = count_by_su(buildings)

    exposure_by_type: dict[str, dict[str, int]] = {}
    if buildings is not None and {"SU_ID", "EXP_TYPE"}.issubset(buildings.columns):
        for _, row in buildings[["SU_ID", "EXP_TYPE"]].dropna().iterrows():
            su = str(row["SU_ID"])
            exp = str(row["EXP_TYPE"]).upper()
            exposure_by_type.setdefault(su, {"SOURCE": 0, "RUNOUT": 0, "BOTH": 0})
            exposure_by_type[su][exp] = exposure_by_type[su].get(exp, 0) + 1

    summary = {}
    for _, row in slopes.iterrows():
        su = str(row["SU_ID"])
        summary[su] = {
            "SU_ID": row["SU_ID"].item() if hasattr(row["SU_ID"], "item") else row["SU_ID"],
            "source_count": int(source_counts.get(su, 0)),
            "path_count": int(path_counts.get(su, 0)),
            "envelope_count": int(envelope_counts.get(su, 0)),
            "exposed_building_count": int(building_counts.get(su, 0)),
            "exposure_by_type": exposure_by_type.get(su, {"SOURCE": 0, "RUNOUT": 0, "BOTH": 0}),
        }
    return summary


def write_json(name: str, data):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    print(f"[OK] Wrote {path}")


def _geom_type_counts(gdf):
    if gdf is None or gdf.empty:
        return {}
    return gdf.geometry.geom_type.value_counts(dropna=False).to_dict()


def _bounds(gdf):
    if gdf is None or gdf.empty:
        return None
    minx, miny, maxx, maxy = gdf.total_bounds
    return [float(minx), float(miny), float(maxx), float(maxy)]


def _missing_required_fields(gdf, required_fields):
    if gdf is None:
        return list(required_fields)
    return [field for field in required_fields if field not in gdf.columns]


def _null_required_fields(gdf, required_fields):
    if gdf is None:
        return {}
    return {field: int(gdf[field].isna().sum()) for field in required_fields if field in gdf.columns and int(gdf[field].isna().sum()) > 0}


def _invalid_geometry_count(gdf):
    if gdf is None or gdf.empty:
        return 0
    return int((~gdf.geometry.is_valid).sum())


def _empty_geometry_count(gdf):
    if gdf is None or gdf.empty:
        return 0
    return int(gdf.geometry.is_empty.sum())


def _join_gap_count(child, child_field: str, parent_values: set[str]):
    if child is None or child_field not in child.columns:
        return 0
    child_values = set(_normalise_id_series(child[child_field].dropna()))
    return len(child_values - parent_values)


def build_export_validation(layers, ridges, selected_su):
    slope_ids = set(_normalise_id_series(layers["slope_units"]["SU_ID"].dropna())) if layers.get("slope_units") is not None else set()
    source_ids = set(_normalise_id_series(layers["source_zones"]["SOURCE_ID"].dropna())) if layers.get("source_zones") is not None else set()

    layer_reports = {}
    for name, gdf in {**layers, "ridges": ridges}.items():
        required_fields = INPUTS.get(name, {}).get("required_fields", [])
        layer_reports[name] = {
            "required": INPUTS.get(name, {}).get("required", False),
            "present": gdf is not None,
            "feature_count": 0 if gdf is None else int(len(gdf)),
            "crs": None if gdf is None or gdf.crs is None else str(gdf.crs),
            "geometry_types": _geom_type_counts(gdf),
            "bounds": _bounds(gdf),
            "missing_required_fields": _missing_required_fields(gdf, required_fields),
            "null_required_fields": _null_required_fields(gdf, required_fields),
            "invalid_geometry_count": _invalid_geometry_count(gdf),
            "empty_geometry_count": _empty_geometry_count(gdf),
        }

    joins = {
        "source_zones.SU_ID -> slope_units.SU_ID": _join_gap_count(layers.get("source_zones"), "SU_ID", slope_ids),
        "runout_paths.SU_ID -> slope_units.SU_ID": _join_gap_count(layers.get("runout_paths"), "SU_ID", slope_ids),
        "runout_envelopes.SU_ID -> slope_units.SU_ID": _join_gap_count(layers.get("runout_envelopes"), "SU_ID", slope_ids),
        "exposed_buildings.SU_ID -> slope_units.SU_ID": _join_gap_count(layers.get("exposed_buildings"), "SU_ID", slope_ids),
        "runout_paths.SOURCE_ID -> source_zones.SOURCE_ID": _join_gap_count(layers.get("runout_paths"), "SOURCE_ID", source_ids),
        "runout_envelopes.SOURCE_ID -> source_zones.SOURCE_ID": _join_gap_count(layers.get("runout_envelopes"), "SOURCE_ID", source_ids),
    }
    errors = []
    warnings = []
    for name, report in layer_reports.items():
        if report["required"] and not report["present"]:
            errors.append(f"Required layer missing: {name}")
        if report["missing_required_fields"]:
            errors.append(f"{name} missing required fields: {', '.join(report['missing_required_fields'])}")
        if report["invalid_geometry_count"]:
            warnings.append(f"{name} contains {report['invalid_geometry_count']} invalid geometries")
        if report["empty_geometry_count"]:
            warnings.append(f"{name} contains {report['empty_geometry_count']} empty geometries")
    for join_name, gap_count in joins.items():
        if gap_count:
            errors.append(f"Join gap: {join_name} has {gap_count} unmatched ID values")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if not errors else "fail",
        "sample_export": selected_su is not None,
        "analysis_crs_expected": ANALYSIS_CRS,
        "display_crs_expected": DISPLAY_CRS,
        "layers": layer_reports,
        "joins": joins,
        "errors": errors,
        "warnings": warnings,
    }


def parse_args(argv: Iterable[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-size", type=int, default=None, help="Export an initial sample of N slope units.")
    parser.add_argument("--su-ids", nargs="*", default=None, help="Explicit SU_ID values to export.")
    parser.add_argument("--skip-ridges", action="store_true", help="Do not export optional ridges layer.")
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None):
    args = parse_args(argv)

    slopes = merge_exposure_summary(read_layer("slope_units"))
    sources = read_layer("source_zones")
    paths = read_layer("runout_paths")
    envelopes = read_layer("runout_envelopes")
    buildings = read_layer("exposed_buildings")
    ridges = None if args.skip_ridges else read_layer("ridges")

    validate_unique(slopes, "SU_ID", "slope_units")
    validate_unique(sources, "SOURCE_ID", "source_zones")
    validate_unique(paths, "PATH_ID", "runout_paths")

    slope_ids = set(_normalise_id_series(slopes["SU_ID"].dropna()))
    source_ids = set(_normalise_id_series(sources["SOURCE_ID"].dropna()))

    validate_subset(sources, "SU_ID", slope_ids, "source_zones")
    validate_subset(paths, "SU_ID", slope_ids, "runout_paths")
    validate_subset(envelopes, "SU_ID", slope_ids, "runout_envelopes")
    validate_subset(buildings, "SU_ID", slope_ids, "exposed_buildings")
    validate_subset(paths, "SOURCE_ID", source_ids, "runout_paths")
    validate_subset(envelopes, "SOURCE_ID", source_ids, "runout_envelopes")

    selected_su = set(map(str, args.su_ids)) if args.su_ids else choose_sample_su_ids(slopes, sources, args.sample_size)

    layers = {
        "slope_units": filter_by_su(slopes, selected_su),
        "source_zones": filter_by_su(sources, selected_su),
        "runout_paths": filter_by_su(paths, selected_su),
        "runout_envelopes": filter_by_su(envelopes, selected_su),
        "exposed_buildings": filter_by_su(buildings, selected_su),
    }

    for name, gdf in layers.items():
        if gdf is None:
            continue
        display = ensure_display_crs(keep_fields(gdf, INPUTS[name]["fields"]), name)
        write_geojson(display, INPUTS[name]["output"])

    if ridges is not None:
        write_geojson(ensure_display_crs(ridges, "ridges"), INPUTS["ridges"]["output"])

    summary = build_slope_summary(
        layers["slope_units"],
        layers["source_zones"],
        layers["runout_paths"],
        layers["runout_envelopes"],
        layers["exposed_buildings"],
    )
    write_json("slope_summary.json", summary)

    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analysis_crs": ANALYSIS_CRS,
        "display_crs": DISPLAY_CRS,
        "source": "Existing PMRDA QGIS/Python analytical outputs; browser does not recompute landslide/source/runout/exposure metrics.",
        "scenario_parameters": {
            "segmentation_min_slope_deg": 20.0,
            "source_min_slope_deg": 20.0,
            "source_susceptibility_classes": [4, 5],
            "source_min_area_m2": 5000.0,
            "source_connectivity": "candidate source zones are associated to slope units by SU_ID in preprocessing",
            "runout_neighbourhood": "D8/down-gradient stored polyline paths from analytical output",
            "runout_reach_angle_deg": 12.0,
            "reach_angle_min_distance_m": 30.0,
            "low_slope_stop_deg": 5.0,
            "low_slope_consecutive_cells": 5,
            "maximum_runout_m": 3000.0,
            "maximum_seeds_per_source": 30,
            "minimum_seed_spacing_m": 30.0,
            "runout_half_width_m": 30.0,
            "model_type": "regional deterministic screening / stored geometry visualisation",
            "animation_is_physical_time": False,
        },
        "warnings": [
            "Regional screening output, not engineering-scale design or real-time simulation.",
            "Susceptibility fields are descriptors, not failure probabilities.",
            "Runout animation is a progressive reveal of stored path geometry, not travel-time or velocity modelling.",
            "Building exposure means footprint intersection with screening geometry, not damage or risk.",
            "Metric attributes were calculated in the analytical CRS and must not be recalculated from EPSG:4326 display geometry.",
        ],
        "export_counts": {name: (0 if gdf is None else int(len(gdf))) for name, gdf in layers.items()},
        "sample_export": selected_su is not None,
    }
    write_json("model_metadata.json", metadata)

    validation = build_export_validation(layers, ridges, selected_su)
    write_json("dashboard_export_validation.json", validation)


if __name__ == "__main__":
    main()
