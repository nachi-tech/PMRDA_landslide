# -*- coding: utf-8 -*-
"""
PMRDA - Building Exposure and Slope Consequence Summary
=======================================================

Purpose
-------
Compute building exposure to:

    A) candidate landslide source zones
    B) downslope runout envelopes

and aggregate the results back to each slope unit.

This script deliberately DOES NOT create a final combined hazard/risk score.
It preserves hazard/susceptibility and exposure as separate quantities so the
ranking method can be chosen after inspecting the actual PMRDA distributions.

Required QGIS layers
--------------------
    slope_units_master
    candidate_source_zones
    runout_envelopes
    PMRDA_Building

Expected fields
---------------
slope_units_master:
    SU_ID
    plus terrain/susceptibility fields if available

candidate_source_zones:
    SOURCE_ID
    SU_ID

runout_envelopes:
    SOURCE_ID
    SU_ID
    MAX_LEN_M
    MAX_DROP_M

PMRDA_Building:
    no building-ID field is required
    QGIS feature ID is written as BUILD_FID

Outputs
-------
1. building_exposure.gpkg
   layer: building_exposure

   One feature = one BUILDING x SLOPE-UNIT exposure relationship.

   Fields:
       BUILD_FID
       SU_ID
       EXP_TYPE          SOURCE / RUNOUT / BOTH
       BLDG_AREA
       SRC_OVLP
       RUN_OVLP
       TOT_OVLP
       OVLP_PCT

2. slope_exposure_summary.gpkg
   layer: slope_exposure_summary

   One polygon = one slope unit.

   Adds:
       N_BLD_SRC
       N_BLD_RUN
       N_BLD_BOTH
       N_BLD_TOT
       BLD_FP_M2
       SRC_OV_M2
       RUN_OV_M2
       TOT_OV_M2
       MEAN_OV_P
       MAX_OV_P
       MAX_RUN_M
       MAX_DROP_M

Methodological details
----------------------
- Source zones are dissolved by SU_ID.
- Runout envelopes are dissolved by SU_ID.
- Runout-only exposure is computed as:

      runout envelope MINUS source-zone geometry

  because the runout envelope may already include the source polygon.

- Building counts are UNIQUE within each slope unit.
- A building can legitimately appear more than once in building_exposure if it
  is exposed to different slope units.
- Building geometries are queried in their native CRS using a QgsSpatialIndex.
  Only candidate buildings are transformed to the analysis CRS, avoiding a
  full reprojection of ~850k buildings.

Run this inside the QGIS Python console/editor.
"""

import os
import csv
import math
from collections import defaultdict
from datetime import datetime

import numpy as np
from osgeo import ogr, osr, gdal

from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsFeatureRequest,
    QgsSpatialIndex,
    QgsGeometry,
    QgsCoordinateTransform,
    QgsCoordinateReferenceSystem
)

gdal.UseExceptions()


# ================================================================
# USER SETTINGS
# ================================================================

SLOPE_LAYER_NAME = "slope_units_master"
SOURCE_LAYER_NAME = "candidate_source_zones"
RUNOUT_LAYER_NAME = "runout_envelopes"
BUILDING_LAYER_NAME = "PMRDA_Building"

OUTPUT_FOLDER = r"F:/sim_repos/PMRDA_landslide/data"

BUILDING_OUTPUT_GPKG = os.path.join(
    OUTPUT_FOLDER,
    "building_exposure.gpkg"
)
BUILDING_OUTPUT_LAYER = "building_exposure"

SLOPE_OUTPUT_GPKG = os.path.join(
    OUTPUT_FOLDER,
    "slope_exposure_summary.gpkg"
)
SLOPE_OUTPUT_LAYER = "slope_exposure_summary"

DIAGNOSTICS_CSV = os.path.join(
    OUTPUT_FOLDER,
    "slope_exposure_diagnostics.csv"
)

OVERWRITE_OUTPUT = True

# Minimum meaningful intersection.
# 1 m² suppresses tiny boundary touches/numerical slivers.
MIN_OVERLAP_M2 = 1.0

# Optional additional percentage threshold.
# 0 means only MIN_OVERLAP_M2 is applied.
MIN_OVERLAP_PCT = 0.0

# Candidate building batches retrieved from provider.
CANDIDATE_BATCH_SIZE = 5000

# Progress interval by slope unit.
PRINT_EVERY_N_SLOPES = 100

os.makedirs(OUTPUT_FOLDER, exist_ok=True)


# ================================================================
# HELPERS
# ================================================================

def get_unique_layer(name):
    layers = QgsProject.instance().mapLayersByName(name)

    if not layers:
        raise RuntimeError(
            f"Required QGIS layer not found: {name}"
        )

    if len(layers) > 1:
        sources = "\n".join(
            f"  - {lyr.source()}"
            for lyr in layers
        )
        raise RuntimeError(
            f"More than one QGIS layer is named '{name}'.\n"
            "Rename/remove duplicates before running this script.\n"
            f"{sources}"
        )

    return layers[0]


def remove_loaded_layers_for_path(path):
    target = os.path.normcase(
        os.path.abspath(path)
    )

    for layer in list(
        QgsProject.instance().mapLayers().values()
    ):
        try:
            src = layer.source().split("|")[0]
            src = os.path.normcase(
                os.path.abspath(src)
            )

            if src == target:
                QgsProject.instance().removeMapLayer(
                    layer.id()
                )
        except Exception:
            pass


def delete_file_if_possible(path):
    if not os.path.exists(path):
        return True

    remove_loaded_layers_for_path(path)

    for suffix in ("-wal", "-shm"):
        p = path + suffix
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    try:
        os.remove(path)
        return True
    except Exception:
        return False


def choose_output_path(path):
    if not os.path.exists(path):
        return path

    if OVERWRITE_OUTPUT and delete_file_if_possible(path):
        return path

    root, ext = os.path.splitext(path)
    stamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )
    fallback = f"{root}_{stamp}{ext}"

    print(
        "\nWARNING: Existing output is locked "
        "or overwrite is disabled."
    )
    print("Using fallback:", fallback)

    return fallback


def safe_geometry(geom):
    if geom is None or geom.isEmpty():
        return None

    g = QgsGeometry(geom)

    try:
        if not g.isGeosValid():
            g = g.makeValid()
    except Exception:
        pass

    if g is None or g.isEmpty():
        return None

    return g


def safe_union(geometries):
    cleaned = []

    for geom in geometries:
        g = safe_geometry(geom)
        if g is not None:
            cleaned.append(g)

    if not cleaned:
        return None

    if len(cleaned) == 1:
        return QgsGeometry(cleaned[0])

    try:
        out = QgsGeometry.unaryUnion(cleaned)
    except Exception:
        out = None

    if out is None or out.isEmpty():
        # Conservative fallback: iterative combine.
        out = QgsGeometry(cleaned[0])

        for g in cleaned[1:]:
            try:
                candidate = QgsGeometry.unaryUnion(
                    [out, g]
                )
                if candidate is not None and not candidate.isEmpty():
                    out = candidate
            except Exception:
                pass

    return safe_geometry(out)


def chunked(seq, size):
    seq = list(seq)

    for i in range(
        0,
        len(seq),
        size
    ):
        yield seq[i:i + size]


def qgis_to_ogr_geometry(qgeom):
    if qgeom is None or qgeom.isEmpty():
        return None

    try:
        return ogr.CreateGeometryFromWkb(
            bytes(qgeom.asWkb())
        )
    except Exception:
        return None


def make_osr_from_qgis(crs):
    srs = osr.SpatialReference()

    authid = crs.authid()

    if authid.upper().startswith("EPSG:"):
        code = int(
            authid.split(":")[1]
        )
        srs.ImportFromEPSG(code)
    else:
        srs.ImportFromWkt(
            crs.toWkt()
        )

    return srs


def add_ogr_field(layer, name, field_type, width=None, precision=None):
    fld = ogr.FieldDefn(
        name,
        field_type
    )

    if width is not None:
        fld.SetWidth(width)

    if precision is not None:
        fld.SetPrecision(precision)

    rc = layer.CreateField(fld)

    if rc != 0:
        raise RuntimeError(
            f"Could not create field '{name}'."
        )


def set_ogr_value(feature, name, value):
    if value is None:
        return

    if isinstance(value, np.integer):
        value = int(value)
    elif isinstance(value, np.floating):
        value = float(value)

    if isinstance(value, float) and not math.isfinite(value):
        return

    feature.SetField(
        name,
        value
    )


def create_building_output(path, layer_name, crs):
    driver = ogr.GetDriverByName(
        "GPKG"
    )

    ds = driver.CreateDataSource(path)

    if ds is None:
        raise RuntimeError(
            f"Could not create:\n{path}"
        )

    layer = ds.CreateLayer(
        layer_name,
        srs=make_osr_from_qgis(crs),
        geom_type=ogr.wkbMultiPolygon
    )

    if layer is None:
        raise RuntimeError(
            "Could not create building exposure layer."
        )

    add_ogr_field(
        layer,
        "BUILD_FID",
        ogr.OFTInteger64
    )
    add_ogr_field(
        layer,
        "SU_ID",
        ogr.OFTInteger64
    )
    add_ogr_field(
        layer,
        "EXP_TYPE",
        ogr.OFTString,
        width=12
    )

    for name in (
        "BLDG_AREA",
        "SRC_OVLP",
        "RUN_OVLP",
        "TOT_OVLP",
        "OVLP_PCT",
    ):
        add_ogr_field(
            layer,
            name,
            ogr.OFTReal,
            width=20,
            precision=4
        )

    return ds, layer


def create_slope_output(path, layer_name, crs):
    driver = ogr.GetDriverByName(
        "GPKG"
    )

    ds = driver.CreateDataSource(path)

    if ds is None:
        raise RuntimeError(
            f"Could not create:\n{path}"
        )

    layer = ds.CreateLayer(
        layer_name,
        srs=make_osr_from_qgis(crs),
        geom_type=ogr.wkbMultiPolygon
    )

    if layer is None:
        raise RuntimeError(
            "Could not create slope exposure summary layer."
        )

    # Existing slope/hazard fields to preserve where available.
    add_ogr_field(layer, "SU_ID", ogr.OFTInteger64)

    real_fields = [
        "AREA_HA",
        "S_MEAN",
        "S_P75",
        "S_P90",
        "S_MAX",
        "ASP_MEAN",
        "ASP_CONC",
        "Z_MIN",
        "Z_MAX",
        "RELIEF",
        "CURV_MEAN",
        "MEAN_LSI",
        "PCT_C1",
        "PCT_C2",
        "PCT_C3",
        "PCT_C4",
        "PCT_C5",
        "HIGH_PCT",
    ]

    for name in real_fields:
        add_ogr_field(
            layer,
            name,
            ogr.OFTReal,
            width=20,
            precision=4
        )

    add_ogr_field(
        layer,
        "DOM_CLASS",
        ogr.OFTInteger
    )

    # Exposure/consequence fields.
    for name in (
        "N_BLD_SRC",
        "N_BLD_RUN",
        "N_BLD_BOTH",
        "N_BLD_TOT",
    ):
        add_ogr_field(
            layer,
            name,
            ogr.OFTInteger64
        )

    for name in (
        "BLD_FP_M2",
        "SRC_OV_M2",
        "RUN_OV_M2",
        "TOT_OV_M2",
        "MEAN_OV_P",
        "MAX_OV_P",
        "MAX_RUN_M",
        "MAX_DROP_M",
    ):
        add_ogr_field(
            layer,
            name,
            ogr.OFTReal,
            width=20,
            precision=4
        )

    return ds, layer


def get_intersection_area(build_geom, hazard_geom):
    if (
        build_geom is None
        or hazard_geom is None
        or build_geom.isEmpty()
        or hazard_geom.isEmpty()
    ):
        return 0.0

    try:
        if not build_geom.intersects(
            hazard_geom
        ):
            return 0.0

        inter = build_geom.intersection(
            hazard_geom
        )

        if inter is None or inter.isEmpty():
            return 0.0

        area = float(
            inter.area()
        )

        return max(
            0.0,
            area
        )

    except Exception:
        return 0.0


# ================================================================
# LOAD INPUTS
# ================================================================

print("\n====================================================")
print("PMRDA BUILDING EXPOSURE + SLOPE CONSEQUENCE")
print("====================================================")

slope_layer = get_unique_layer(
    SLOPE_LAYER_NAME
)
source_layer = get_unique_layer(
    SOURCE_LAYER_NAME
)
runout_layer = get_unique_layer(
    RUNOUT_LAYER_NAME
)
building_layer = get_unique_layer(
    BUILDING_LAYER_NAME
)

analysis_crs = slope_layer.crs()

if analysis_crs.isGeographic():
    raise RuntimeError(
        "slope_units_master must use a projected metric CRS."
    )

for lyr in (
    source_layer,
    runout_layer,
):
    if lyr.crs() != analysis_crs:
        raise RuntimeError(
            f"{lyr.name()} CRS does not match slope_units_master."
        )

for lyr, fields in (
    (slope_layer, ["SU_ID"]),
    (source_layer, ["SU_ID"]),
    (runout_layer, ["SU_ID"]),
):
    for name in fields:
        if lyr.fields().indexOf(name) < 0:
            raise RuntimeError(
                f"{lyr.name()} is missing field '{name}'."
            )

print("\nInput counts:")
print(
    "  slope units:",
    slope_layer.featureCount()
)
print(
    "  source zones:",
    source_layer.featureCount()
)
print(
    "  runout envelopes:",
    runout_layer.featureCount()
)
print(
    "  buildings:",
    building_layer.featureCount()
)

print("\nCRS:")
print(
    "  analysis:",
    analysis_crs.authid()
)
print(
    "  buildings:",
    building_layer.crs().authid()
)


# ================================================================
# DISSOLVE SOURCE + RUNOUT BY SLOPE UNIT
# ================================================================

print("\nGrouping source zones by SU_ID...")

source_groups = defaultdict(list)

for feat in source_layer.getFeatures():
    try:
        su_id = int(
            feat["SU_ID"]
        )
    except Exception:
        continue

    geom = safe_geometry(
        feat.geometry()
    )

    if geom is not None:
        source_groups[su_id].append(
            geom
        )

print(
    "Slope units with source zones:",
    len(source_groups)
)

print("\nGrouping runout envelopes by SU_ID...")

runout_groups = defaultdict(list)

# Also capture runout metrics.
runout_max_len = defaultdict(float)
runout_max_drop = defaultdict(float)

run_len_idx = runout_layer.fields().indexOf(
    "MAX_LEN_M"
)
run_drop_idx = runout_layer.fields().indexOf(
    "MAX_DROP_M"
)

for feat in runout_layer.getFeatures():
    try:
        su_id = int(
            feat["SU_ID"]
        )
    except Exception:
        continue

    geom = safe_geometry(
        feat.geometry()
    )

    if geom is not None:
        runout_groups[su_id].append(
            geom
        )

    if run_len_idx >= 0:
        try:
            v = float(
                feat["MAX_LEN_M"]
            )
            if math.isfinite(v):
                runout_max_len[su_id] = max(
                    runout_max_len[su_id],
                    v
                )
        except Exception:
            pass

    if run_drop_idx >= 0:
        try:
            v = float(
                feat["MAX_DROP_M"]
            )
            if math.isfinite(v):
                runout_max_drop[su_id] = max(
                    runout_max_drop[su_id],
                    v
                )
        except Exception:
            pass

print(
    "Slope units with runout envelopes:",
    len(runout_groups)
)

all_hazard_su_ids = sorted(
    set(source_groups)
    | set(runout_groups)
)

print(
    "Slope units requiring exposure analysis:",
    len(all_hazard_su_ids)
)


# ================================================================
# PREPARE DISSOLVED GEOMETRIES
# ================================================================

print("\nDissolving source/runout geometry by SU_ID...")

hazard_geoms = {}

for i, su_id in enumerate(
    all_hazard_su_ids,
    start=1
):
    src = safe_union(
        source_groups.get(
            su_id,
            []
        )
    )

    run = safe_union(
        runout_groups.get(
            su_id,
            []
        )
    )

    # Runout envelopes may already contain the source zone.
    # Remove source footprint so SOURCE and RUNOUT are distinct.
    run_only = None

    if run is not None:
        if src is not None:
            try:
                run_only = run.difference(
                    src
                )
                run_only = safe_geometry(
                    run_only
                )
            except Exception:
                run_only = run
        else:
            run_only = run

    total = safe_union(
        [
            g for g in (
                src,
                run_only
            )
            if g is not None
        ]
    )

    if total is not None:
        hazard_geoms[su_id] = {
            "source": src,
            "runout": run_only,
            "total": total,
        }

    if i % 500 == 0:
        print(
            f"  dissolved {i}/"
            f"{len(all_hazard_su_ids)}"
        )

print(
    "Usable dissolved hazard slope units:",
    len(hazard_geoms)
)


# ================================================================
# BUILD BUILDING SPATIAL INDEX
# ================================================================

print("\nBuilding spatial index...")

building_index = QgsSpatialIndex(
    building_layer.getFeatures()
)

print("Building spatial index complete.")


# ================================================================
# COORDINATE TRANSFORMS
# ================================================================

project_context = (
    QgsProject.instance()
    .transformContext()
)

analysis_to_build = QgsCoordinateTransform(
    analysis_crs,
    building_layer.crs(),
    project_context
)

build_to_analysis = QgsCoordinateTransform(
    building_layer.crs(),
    analysis_crs,
    project_context
)


# ================================================================
# CREATE OUTPUTS
# ================================================================

actual_building_gpkg = choose_output_path(
    BUILDING_OUTPUT_GPKG
)
actual_slope_gpkg = choose_output_path(
    SLOPE_OUTPUT_GPKG
)

building_ds, building_out = create_building_output(
    actual_building_gpkg,
    BUILDING_OUTPUT_LAYER,
    analysis_crs
)

slope_ds, slope_out = create_slope_output(
    actual_slope_gpkg,
    SLOPE_OUTPUT_LAYER,
    analysis_crs
)


# ================================================================
# EXPOSURE ANALYSIS
# ================================================================

print("\nCalculating building exposure...")

# Per-slope metrics.
summary = {}

exposure_record_count = 0

for idx, su_id in enumerate(
    sorted(hazard_geoms),
    start=1
):
    geoms = hazard_geoms[su_id]

    source_geom = geoms["source"]
    runout_geom = geoms["runout"]
    total_geom = geoms["total"]

    if total_geom is None or total_geom.isEmpty():
        continue

    # Query building index in the BUILDING layer CRS.
    try:
        search_bbox = analysis_to_build.transformBoundingBox(
            total_geom.boundingBox()
        )
    except Exception as exc:
        print(
            f"WARNING: bbox transform failed for SU_ID {su_id}: {exc}"
        )
        continue

    candidate_ids = building_index.intersects(
        search_bbox
    )

    if not candidate_ids:
        summary[su_id] = {
            "n_src": 0,
            "n_run": 0,
            "n_both": 0,
            "n_total": 0,
            "footprint": 0.0,
            "src_overlap": 0.0,
            "run_overlap": 0.0,
            "total_overlap": 0.0,
            "overlap_pcts": [],
        }
        continue

    metric = {
        "n_src": 0,
        "n_run": 0,
        "n_both": 0,
        "n_total": 0,
        "footprint": 0.0,
        "src_overlap": 0.0,
        "run_overlap": 0.0,
        "total_overlap": 0.0,
        "overlap_pcts": [],
    }

    for id_batch in chunked(
        candidate_ids,
        CANDIDATE_BATCH_SIZE
    ):
        request = QgsFeatureRequest()
        request.setFilterFids(
            id_batch
        )

        for bfeat in building_layer.getFeatures(
            request
        ):
            bgeom = safe_geometry(
                bfeat.geometry()
            )

            if bgeom is None:
                continue

            try:
                bgeom.transform(
                    build_to_analysis
                )
            except Exception:
                continue

            if bgeom.isEmpty():
                continue

            building_area = float(
                bgeom.area()
            )

            if building_area <= 0:
                continue

            # Fast total test before detailed source/runout intersections.
            try:
                if not bgeom.intersects(
                    total_geom
                ):
                    continue
            except Exception:
                continue

            src_area = get_intersection_area(
                bgeom,
                source_geom
            )

            run_area = get_intersection_area(
                bgeom,
                runout_geom
            )

            src_exposed = (
                src_area >= MIN_OVERLAP_M2
                and (
                    MIN_OVERLAP_PCT <= 0.0
                    or (
                        100.0
                        * src_area
                        / building_area
                    ) >= MIN_OVERLAP_PCT
                )
            )

            run_exposed = (
                run_area >= MIN_OVERLAP_M2
                and (
                    MIN_OVERLAP_PCT <= 0.0
                    or (
                        100.0
                        * run_area
                        / building_area
                    ) >= MIN_OVERLAP_PCT
                )
            )

            if not (
                src_exposed
                or run_exposed
            ):
                continue

            if src_exposed and run_exposed:
                exp_type = "BOTH"
                metric["n_both"] += 1
            elif src_exposed:
                exp_type = "SOURCE"
                metric["n_src"] += 1
            else:
                exp_type = "RUNOUT"
                metric["n_run"] += 1

            metric["n_total"] += 1
            metric["footprint"] += building_area

            accepted_src_area = (
                src_area
                if src_exposed
                else 0.0
            )

            accepted_run_area = (
                run_area
                if run_exposed
                else 0.0
            )

            total_overlap = (
                accepted_src_area
                + accepted_run_area
            )

            overlap_pct = min(
                100.0,
                100.0
                * total_overlap
                / building_area
            )

            metric["src_overlap"] += (
                accepted_src_area
            )
            metric["run_overlap"] += (
                accepted_run_area
            )
            metric["total_overlap"] += (
                total_overlap
            )
            metric["overlap_pcts"].append(
                overlap_pct
            )

            # ------------------------------------------------
            # Write BUILDING x SLOPE relationship
            # ------------------------------------------------
            out_feat = ogr.Feature(
                building_out.GetLayerDefn()
            )

            set_ogr_value(
                out_feat,
                "BUILD_FID",
                int(bfeat.id())
            )
            set_ogr_value(
                out_feat,
                "SU_ID",
                int(su_id)
            )
            set_ogr_value(
                out_feat,
                "EXP_TYPE",
                exp_type
            )
            set_ogr_value(
                out_feat,
                "BLDG_AREA",
                building_area
            )
            set_ogr_value(
                out_feat,
                "SRC_OVLP",
                accepted_src_area
            )
            set_ogr_value(
                out_feat,
                "RUN_OVLP",
                accepted_run_area
            )
            set_ogr_value(
                out_feat,
                "TOT_OVLP",
                total_overlap
            )
            set_ogr_value(
                out_feat,
                "OVLP_PCT",
                overlap_pct
            )

            ogr_geom = qgis_to_ogr_geometry(
                bgeom
            )

            if ogr_geom is None:
                out_feat = None
                continue

            # Ensure polygon output can accept single/multipart.
            if ogr_geom.GetGeometryType() in (
                ogr.wkbPolygon,
                ogr.wkbPolygon25D,
            ):
                multi = ogr.Geometry(
                    ogr.wkbMultiPolygon
                )
                multi.AddGeometry(
                    ogr_geom
                )
                ogr_geom = multi

            out_feat.SetGeometry(
                ogr_geom
            )

            rc = building_out.CreateFeature(
                out_feat
            )

            if rc != 0:
                raise RuntimeError(
                    f"Failed writing exposure record "
                    f"for BUILD_FID={bfeat.id()}, SU_ID={su_id}"
                )

            exposure_record_count += 1
            out_feat = None

    summary[su_id] = metric

    if idx % PRINT_EVERY_N_SLOPES == 0:
        print(
            f"  processed {idx}/"
            f"{len(hazard_geoms)} slopes; "
            f"exposure records={exposure_record_count}"
        )

building_out.SyncToDisk()

print(
    "\nBuilding-slope exposure records:",
    exposure_record_count
)


# ================================================================
# WRITE SLOPE SUMMARY
# ================================================================

print("\nWriting slope exposure summary...")

preserve_fields = [
    "AREA_HA",
    "S_MEAN",
    "S_P75",
    "S_P90",
    "S_MAX",
    "ASP_MEAN",
    "ASP_CONC",
    "Z_MIN",
    "Z_MAX",
    "RELIEF",
    "CURV_MEAN",
    "MEAN_LSI",
    "PCT_C1",
    "PCT_C2",
    "PCT_C3",
    "PCT_C4",
    "PCT_C5",
    "HIGH_PCT",
    "DOM_CLASS",
]

available_preserve = {
    name: slope_layer.fields().indexOf(name)
    for name in preserve_fields
}

slope_count = 0

for feat in slope_layer.getFeatures():
    try:
        su_id = int(
            feat["SU_ID"]
        )
    except Exception:
        continue

    geom = safe_geometry(
        feat.geometry()
    )

    if geom is None:
        continue

    metric = summary.get(
        su_id,
        {
            "n_src": 0,
            "n_run": 0,
            "n_both": 0,
            "n_total": 0,
            "footprint": 0.0,
            "src_overlap": 0.0,
            "run_overlap": 0.0,
            "total_overlap": 0.0,
            "overlap_pcts": [],
        }
    )

    pcts = metric[
        "overlap_pcts"
    ]

    mean_ov = (
        float(np.mean(pcts))
        if pcts
        else 0.0
    )

    max_ov = (
        float(np.max(pcts))
        if pcts
        else 0.0
    )

    out_feat = ogr.Feature(
        slope_out.GetLayerDefn()
    )

    set_ogr_value(
        out_feat,
        "SU_ID",
        su_id
    )

    for name in preserve_fields:
        if available_preserve[
            name
        ] >= 0:
            value = feat[name]

            try:
                if value is not None:
                    if name == "DOM_CLASS":
                        value = int(value)
                    else:
                        value = float(value)
            except Exception:
                value = None

            set_ogr_value(
                out_feat,
                name,
                value
            )

    set_ogr_value(
        out_feat,
        "N_BLD_SRC",
        int(metric["n_src"])
    )
    set_ogr_value(
        out_feat,
        "N_BLD_RUN",
        int(metric["n_run"])
    )
    set_ogr_value(
        out_feat,
        "N_BLD_BOTH",
        int(metric["n_both"])
    )
    set_ogr_value(
        out_feat,
        "N_BLD_TOT",
        int(metric["n_total"])
    )

    set_ogr_value(
        out_feat,
        "BLD_FP_M2",
        float(metric["footprint"])
    )
    set_ogr_value(
        out_feat,
        "SRC_OV_M2",
        float(metric["src_overlap"])
    )
    set_ogr_value(
        out_feat,
        "RUN_OV_M2",
        float(metric["run_overlap"])
    )
    set_ogr_value(
        out_feat,
        "TOT_OV_M2",
        float(metric["total_overlap"])
    )
    set_ogr_value(
        out_feat,
        "MEAN_OV_P",
        mean_ov
    )
    set_ogr_value(
        out_feat,
        "MAX_OV_P",
        max_ov
    )
    set_ogr_value(
        out_feat,
        "MAX_RUN_M",
        float(
            runout_max_len.get(
                su_id,
                0.0
            )
        )
    )
    set_ogr_value(
        out_feat,
        "MAX_DROP_M",
        float(
            runout_max_drop.get(
                su_id,
                0.0
            )
        )
    )

    ogr_geom = qgis_to_ogr_geometry(
        geom
    )

    if ogr_geom is None:
        out_feat = None
        continue

    if ogr_geom.GetGeometryType() in (
        ogr.wkbPolygon,
        ogr.wkbPolygon25D,
    ):
        multi = ogr.Geometry(
            ogr.wkbMultiPolygon
        )
        multi.AddGeometry(
            ogr_geom
        )
        ogr_geom = multi

    out_feat.SetGeometry(
        ogr_geom
    )

    rc = slope_out.CreateFeature(
        out_feat
    )

    if rc != 0:
        raise RuntimeError(
            f"Failed writing slope summary for SU_ID={su_id}"
        )

    out_feat = None
    slope_count += 1

slope_out.SyncToDisk()

print(
    "Slope summary polygons written:",
    slope_count
)


# ================================================================
# DIAGNOSTICS CSV
# ================================================================

print("\nWriting diagnostics CSV...")

rows = []

for su_id, metric in summary.items():
    pcts = metric[
        "overlap_pcts"
    ]

    rows.append({
        "SU_ID": su_id,
        "N_BLD_SRC": metric["n_src"],
        "N_BLD_RUN": metric["n_run"],
        "N_BLD_BOTH": metric["n_both"],
        "N_BLD_TOT": metric["n_total"],
        "BLD_FP_M2": metric["footprint"],
        "SRC_OV_M2": metric["src_overlap"],
        "RUN_OV_M2": metric["run_overlap"],
        "TOT_OV_M2": metric["total_overlap"],
        "MEAN_OV_P": (
            float(np.mean(pcts))
            if pcts
            else 0.0
        ),
        "MAX_OV_P": (
            float(np.max(pcts))
            if pcts
            else 0.0
        ),
        "MAX_RUN_M": runout_max_len.get(
            su_id,
            0.0
        ),
        "MAX_DROP_M": runout_max_drop.get(
            su_id,
            0.0
        ),
    })

rows.sort(
    key=lambda r: (
        r["N_BLD_TOT"],
        r["TOT_OV_M2"]
    ),
    reverse=True
)

with open(
    DIAGNOSTICS_CSV,
    "w",
    newline="",
    encoding="utf-8"
) as f:
    writer = csv.DictWriter(
        f,
        fieldnames=[
            "SU_ID",
            "N_BLD_SRC",
            "N_BLD_RUN",
            "N_BLD_BOTH",
            "N_BLD_TOT",
            "BLD_FP_M2",
            "SRC_OV_M2",
            "RUN_OV_M2",
            "TOT_OV_M2",
            "MEAN_OV_P",
            "MAX_OV_P",
            "MAX_RUN_M",
            "MAX_DROP_M",
        ]
    )

    writer.writeheader()
    writer.writerows(
        rows
    )


# ================================================================
# PRINT DISTRIBUTIONS
# ================================================================

counts = np.array(
    [
        r["N_BLD_TOT"]
        for r in rows
    ],
    dtype=np.float64
)

print("\n====================================================")
print("EXPOSURE ANALYSIS COMPLETE")
print("====================================================")

print("\nOutputs:")
print(
    "Building exposure:",
    actual_building_gpkg
)
print(
    "Slope summary    :",
    actual_slope_gpkg
)
print(
    "Diagnostics CSV  :",
    DIAGNOSTICS_CSV
)

if counts.size:
    print("\nN_BLD_TOT distribution among analysed hazard slopes:")

    for q in (
        0,
        25,
        50,
        75,
        90,
        95,
        99,
        100,
    ):
        print(
            f"  P{q:02d}: "
            f"{np.percentile(counts, q):.1f}"
        )

    bins = [
        (0, 0),
        (1, 10),
        (11, 50),
        (51, 100),
        (101, 250),
        (251, 500),
        (501, float("inf")),
    ]

    print("\nSlope counts by exposed-building range:")

    for lo, hi in bins:
        if math.isinf(hi):
            n = int(
                np.sum(
                    counts >= lo
                )
            )
            label = f">={lo}"
        else:
            n = int(
                np.sum(
                    (counts >= lo)
                    & (counts <= hi)
                )
            )
            label = (
                f"{int(lo)}"
                if lo == hi
                else f"{int(lo)}-{int(hi)}"
            )

        print(
            f"  {label:>8}: {n}"
        )

print("\nTop 20 hazard slopes by exposed building count:")

for r in rows[:20]:
    print(
        "  SU_ID={:<7}  buildings={:<6} "
        "overlap_m2={:<12.1f} max_runout_m={:.1f}".format(
            r["SU_ID"],
            r["N_BLD_TOT"],
            r["TOT_OV_M2"],
            r["MAX_RUN_M"],
        )
    )

print(
    "\nInterpretation:"
    "\n- N_BLD_TOT is exposure, not final risk."
    "\n- HIGH_PCT / MEAN_LSI remain hazard/susceptibility."
    "\n- The next step is to inspect these distributions and build "
    "a transparent consequence + priority ranking rather than "
    "choosing arbitrary weights in advance."
)

print("\nDone.")

# Close OGR outputs before loading them in QGIS.
building_out = None
building_ds = None
slope_out = None
slope_ds = None


# ================================================================
# LOAD OUTPUTS INTO QGIS
# ================================================================

building_uri = (
    f"{actual_building_gpkg}"
    f"|layername={BUILDING_OUTPUT_LAYER}"
)

slope_uri = (
    f"{actual_slope_gpkg}"
    f"|layername={SLOPE_OUTPUT_LAYER}"
)

building_result = QgsVectorLayer(
    building_uri,
    BUILDING_OUTPUT_LAYER,
    "ogr"
)

slope_result = QgsVectorLayer(
    slope_uri,
    SLOPE_OUTPUT_LAYER,
    "ogr"
)

if building_result.isValid():
    QgsProject.instance().addMapLayer(
        building_result
    )
else:
    print(
        "WARNING: building exposure output was written "
        "but could not be loaded automatically."
    )

if slope_result.isValid():
    QgsProject.instance().addMapLayer(
        slope_result
    )
else:
    print(
        "WARNING: slope summary output was written "
        "but could not be loaded automatically."
    )



