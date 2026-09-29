# -*- coding: utf-8 -*-
"""
PMRDA - Regional Landslide Runout Screening Model
=================================================

Purpose
-------
Generate first-pass downslope runout trajectories and runout envelopes from
candidate_source_zones.

This is NOT a physically based debris-flow/landslide dynamics model.
It is a regional screening proxy using:
    - DEM-based steepest descent (8-neighbour/D8-style)
    - angle-of-reach stopping criterion
    - low-slope stopping criterion
    - maximum travel distance
    - multiple sampled release seeds per SOURCE_ID

The script deliberately derives the downhill direction directly from dem_filled.
It does NOT assume any encoding for the existing flow_direction raster.

Required QGIS layers
--------------------
    candidate_source_zones
    dem_filled
    slope_deg

Expected source fields
----------------------
    SOURCE_ID
    SU_ID

Outputs
-------
    runout_paths.gpkg
        layer: runout_paths

    runout_envelopes.gpkg
        layer: runout_envelopes

Path fields
-----------
    PATH_ID
    SOURCE_ID
    SU_ID
    START_Z
    END_Z
    DROP_M
    LENGTH_M
    REACH_DEG
    N_STEPS
    END_REASON

Envelope fields
---------------
    SOURCE_ID
    SU_ID
    N_PATHS
    MAX_LEN_M
    MAX_DROP_M
    AREA_HA

Default scenario
----------------
    reach angle = 12 degrees
    stop when local terrain slope < 5 degrees for 3 consecutive cells
    max travel distance = 3000 m
    max release seeds per source = 30
    path-envelope half-width = 30 m

Important
---------
These are scenario parameters, not universal landslide constants.
The model should be calibrated/sensitivity-tested later.

Run inside the QGIS Python console/editor.
"""

import os
import math
from datetime import datetime

import numpy as np
from osgeo import gdal, ogr, osr

from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsRasterLayer,
    QgsField,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsVectorFileWriter
)
from qgis.PyQt.QtCore import QVariant


# ================================================================
# USER SETTINGS
# ================================================================

SOURCE_LAYER_NAME = "candidate_source_zones"
DEM_RASTER_NAME = "dem_filled"
SLOPE_RASTER_NAME = "slope_deg"

OUTPUT_FOLDER = r"F:/sim_repos/PMRDA_landslide/data"

PATH_GPKG = os.path.join(
    OUTPUT_FOLDER,
    "runout_paths.gpkg"
)

PATH_LAYER_NAME = "runout_paths"

ENVELOPE_GPKG = os.path.join(
    OUTPUT_FOLDER,
    "runout_envelopes.gpkg"
)

ENVELOPE_LAYER_NAME = "runout_envelopes"

TEMP_FOLDER = os.path.join(
    OUTPUT_FOLDER,
    "temp_runout"
)

# ------------------------------------------------
# Runout scenario parameters
# ------------------------------------------------

REACH_ANGLE_DEG = 12.0

# Do not apply angle-of-reach stop until the path has moved this far.
MIN_REACH_CHECK_DISTANCE_M = 100.0

# Low-gradient terrain stop.
LOW_SLOPE_STOP_DEG = 5.0
LOW_SLOPE_CONSECUTIVE_CELLS = 3

MAX_RUNOUT_DISTANCE_M = 3000.0

# Candidate seed selection:
# only source cells with a lower neighbour outside the source are candidates.
MAX_SEEDS_PER_SOURCE = 30

# Minimum spacing between selected seeds.
MIN_SEED_SPACING_M = 60.0

# Envelope approximation around runout paths.
RUNOUT_HALF_WIDTH_M = 30.0

# Include source polygon itself in the final envelope?
INCLUDE_SOURCE_POLYGON_IN_ENVELOPE = True

# Require strict downhill motion. Recommended.
MIN_ELEVATION_DROP_PER_STEP_M = 0.01

# Rasterization behaviour
ALL_TOUCHED = False

OVERWRITE_OUTPUT = True

os.makedirs(OUTPUT_FOLDER, exist_ok=True)
os.makedirs(TEMP_FOLDER, exist_ok=True)

gdal.UseExceptions()


# ================================================================
# HELPERS
# ================================================================

def get_layer(name):
    layers = QgsProject.instance().mapLayersByName(name)
    if not layers:
        raise RuntimeError(
            f"Required QGIS layer not found: {name}"
        )
    return layers[0]


def raster_path(layer):
    return layer.source().split("|")[0]


def open_raster(layer):
    path = raster_path(layer)
    ds = gdal.Open(path, gdal.GA_ReadOnly)
    if ds is None:
        raise RuntimeError(
            f"GDAL could not open raster:\n{layer.name()}\n{path}"
        )
    return ds


def same_grid(a, b, tol=1e-6):
    if (
        a.RasterXSize != b.RasterXSize
        or a.RasterYSize != b.RasterYSize
    ):
        return False

    g1 = a.GetGeoTransform()
    g2 = b.GetGeoTransform()

    for x, y in zip(g1, g2):
        if abs(x - y) > tol:
            return False

    s1 = osr.SpatialReference()
    s2 = osr.SpatialReference()
    s1.ImportFromWkt(a.GetProjection())
    s2.ImportFromWkt(b.GetProjection())

    return bool(s1.IsSame(s2))


def valid_mask(arr, band):
    mask = np.isfinite(arr)

    nodata = band.GetNoDataValue()
    if nodata is not None:
        try:
            if math.isnan(nodata):
                mask &= ~np.isnan(arr)
            else:
                mask &= arr != nodata
        except Exception:
            mask &= arr != nodata

    return mask


def remove_loaded_layers_for_path(path):
    target = os.path.normcase(os.path.abspath(path))

    for layer in list(QgsProject.instance().mapLayers().values()):
        try:
            src = layer.source().split("|")[0]
            src = os.path.normcase(os.path.abspath(src))
            if src == target:
                QgsProject.instance().removeMapLayer(layer.id())
        except Exception:
            pass


def delete_output(path):
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


def choose_output(path):
    if not os.path.exists(path):
        return path

    if OVERWRITE_OUTPUT and delete_output(path):
        return path

    root, ext = os.path.splitext(path)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fallback = f"{root}_{stamp}{ext}"

    print("\nWARNING: Could not overwrite existing output.")
    print("Using:", fallback)

    return fallback


def parse_ogr_source(qgs_layer):
    src = qgs_layer.source()
    parts = src.split("|")
    base = parts[0]

    layer_name = None

    for part in parts[1:]:
        if part.lower().startswith("layername="):
            layer_name = part.split("=", 1)[1]
            break

    ds = ogr.Open(base, 0)

    if ds is None:
        raise RuntimeError(
            f"OGR could not open source layer:\n{src}"
        )

    lyr = (
        ds.GetLayerByName(layer_name)
        if layer_name
        else ds.GetLayer(0)
    )

    if lyr is None:
        raise RuntimeError(
            f"OGR could not open source sublayer:\n{src}"
        )

    return ds, lyr


def rasterize_source_ids(reference_ds, source_layer, output_tif):
    drv = gdal.GetDriverByName("GTiff")

    ds = drv.Create(
        output_tif,
        reference_ds.RasterXSize,
        reference_ds.RasterYSize,
        1,
        gdal.GDT_Int32,
        options=[
            "COMPRESS=LZW",
            "TILED=YES",
            "BIGTIFF=IF_SAFER"
        ]
    )

    if ds is None:
        raise RuntimeError(
            f"Could not create source-ID raster:\n{output_tif}"
        )

    ds.SetGeoTransform(reference_ds.GetGeoTransform())
    ds.SetProjection(reference_ds.GetProjection())

    band = ds.GetRasterBand(1)
    band.Fill(0)
    band.SetNoDataValue(0)

    vds, vlyr = parse_ogr_source(source_layer)

    options = ["ATTRIBUTE=SOURCE_ID"]

    if ALL_TOUCHED:
        options.append("ALL_TOUCHED=TRUE")

    err = gdal.RasterizeLayer(
        ds,
        [1],
        vlyr,
        options=options
    )

    if err != 0:
        raise RuntimeError(
            "Rasterization of SOURCE_ID failed."
        )

    band.FlushCache()
    ds.FlushCache()

    vlyr = None
    vds = None

    return ds


def cell_center(row, col, gt):
    x = (
        gt[0]
        + (col + 0.5) * gt[1]
        + (row + 0.5) * gt[2]
    )

    y = (
        gt[3]
        + (col + 0.5) * gt[4]
        + (row + 0.5) * gt[5]
    )

    return x, y


def step_distance(dr, dc, gt):
    # Works for north-up rasters; rotation terms are included.
    dx = dc * gt[1] + dr * gt[2]
    dy = dc * gt[4] + dr * gt[5]
    return math.hypot(dx, dy)


NEIGHBOURS = [
    (-1, -1),
    (-1,  0),
    (-1,  1),
    ( 0, -1),
    ( 0,  1),
    ( 1, -1),
    ( 1,  0),
    ( 1,  1),
]


def find_downslope_neighbour(
    row,
    col,
    dem,
    dem_valid,
    gt
):
    """
    Select neighbour with the greatest elevation gradient downhill.
    Returns:
        next_row, next_col, distance, drop, gradient
    or None.
    """
    h, w = dem.shape

    z0 = float(dem[row, col])

    best = None
    best_gradient = -np.inf

    for dr, dc in NEIGHBOURS:
        rr = row + dr
        cc = col + dc

        if rr < 0 or rr >= h or cc < 0 or cc >= w:
            continue

        if not dem_valid[rr, cc]:
            continue

        z1 = float(dem[rr, cc])

        drop = z0 - z1

        if drop < MIN_ELEVATION_DROP_PER_STEP_M:
            continue

        dist = step_distance(
            dr,
            dc,
            gt
        )

        if dist <= 0:
            continue

        gradient = drop / dist

        if gradient > best_gradient:
            best_gradient = gradient
            best = (
                rr,
                cc,
                dist,
                drop,
                gradient
            )

    return best


def is_downslope_edge_cell(
    row,
    col,
    source_id,
    source_ids,
    dem,
    dem_valid,
    gt
):
    """
    A release seed candidate is a source cell that has at least one lower
    neighbouring cell outside its own source polygon.
    """
    h, w = dem.shape
    z0 = float(dem[row, col])

    for dr, dc in NEIGHBOURS:
        rr = row + dr
        cc = col + dc

        if rr < 0 or rr >= h or cc < 0 or cc >= w:
            continue

        if not dem_valid[rr, cc]:
            continue

        if int(source_ids[rr, cc]) == source_id:
            continue

        z1 = float(dem[rr, cc])

        if z0 - z1 >= MIN_ELEVATION_DROP_PER_STEP_M:
            return True

    return False


def select_spaced_seeds(candidates, gt, max_seeds, min_spacing):
    """
    Greedy spatial thinning.

    Candidate tuples:
        (row, col, elevation)

    Higher-elevation candidates are considered first.
    """
    if not candidates:
        return []

    candidates = sorted(
        candidates,
        key=lambda x: x[2],
        reverse=True
    )

    selected = []

    for row, col, z in candidates:
        x, y = cell_center(
            row,
            col,
            gt
        )

        keep = True

        for sr, sc, sz in selected:
            sx, sy = cell_center(
                sr,
                sc,
                gt
            )

            if math.hypot(
                x - sx,
                y - sy
            ) < min_spacing:
                keep = False
                break

        if keep:
            selected.append(
                (row, col, z)
            )

        if len(selected) >= max_seeds:
            break

    return selected


def trace_path(
    seed_row,
    seed_col,
    dem,
    dem_valid,
    slope,
    slope_valid,
    gt
):
    """
    Trace one DEM-driven path.

    Stopping criteria:
        - angle of reach
        - consecutive low-slope cells
        - max distance
        - no lower neighbour
        - raster boundary
    """
    start_z = float(
        dem[seed_row, seed_col]
    )

    row = seed_row
    col = seed_col

    x0, y0 = cell_center(
        row,
        col,
        gt
    )

    points = [
        QgsPointXY(x0, y0)
    ]

    total_length = 0.0
    low_slope_count = 0
    steps = 0
    end_reason = "UNKNOWN"

    # Prevent accidental pathological loops.
    max_steps = int(
        MAX_RUNOUT_DISTANCE_M
        / max(
            abs(gt[1]),
            abs(gt[5])
        )
    ) * 4 + 100

    for _ in range(max_steps):
        if total_length >= MAX_RUNOUT_DISTANCE_M:
            end_reason = "MAX_DISTANCE"
            break

        nxt = find_downslope_neighbour(
            row,
            col,
            dem,
            dem_valid,
            gt
        )

        if nxt is None:
            end_reason = "NO_LOWER_CELL"
            break

        nr, nc, dist, drop, gradient = nxt

        row = nr
        col = nc
        total_length += dist
        steps += 1

        x, y = cell_center(
            row,
            col,
            gt
        )

        points.append(
            QgsPointXY(x, y)
        )

        current_z = float(
            dem[row, col]
        )

        # ------------------------------------------------
        # Low-slope stop
        # ------------------------------------------------
        if slope_valid[row, col]:
            local_slope = float(
                slope[row, col]
            )

            if local_slope < LOW_SLOPE_STOP_DEG:
                low_slope_count += 1
            else:
                low_slope_count = 0

            if (
                low_slope_count
                >= LOW_SLOPE_CONSECUTIVE_CELLS
            ):
                end_reason = "LOW_SLOPE"
                break

        # ------------------------------------------------
        # Angle-of-reach stop
        # ------------------------------------------------
        if (
            total_length
            >= MIN_REACH_CHECK_DISTANCE_M
        ):
            horizontal = math.hypot(
                x - x0,
                y - y0
            )

            vertical_drop = (
                start_z - current_z
            )

            if (
                horizontal > 0
                and vertical_drop > 0
            ):
                reach_deg = math.degrees(
                    math.atan2(
                        vertical_drop,
                        horizontal
                    )
                )

                if reach_deg <= REACH_ANGLE_DEG:
                    end_reason = "REACH_ANGLE"
                    break

    if end_reason == "UNKNOWN":
        end_reason = "MAX_STEPS"

    end_z = float(
        dem[row, col]
    )

    x_end, y_end = cell_center(
        row,
        col,
        gt
    )

    horizontal = math.hypot(
        x_end - x0,
        y_end - y0
    )

    drop_m = (
        start_z - end_z
    )

    if horizontal > 0 and drop_m > 0:
        reach_deg = math.degrees(
            math.atan2(
                drop_m,
                horizontal
            )
        )
    else:
        reach_deg = None

    return {
        "points": points,
        "start_z": start_z,
        "end_z": end_z,
        "drop_m": drop_m,
        "length_m": total_length,
        "reach_deg": reach_deg,
        "n_steps": steps,
        "end_reason": end_reason,
    }



def qgis_scalar(value):
    """
    Convert NumPy scalar values to ordinary Python values before they are
    placed in QgsFeature attributes. QGIS' memory provider may reject
    np.int64 / np.float64 even when the numeric value itself is valid.
    """
    if value is None:
        return None

    if isinstance(value, np.integer):
        return int(value)

    if isinstance(value, np.floating):
        value = float(value)
        return value if math.isfinite(value) else None

    if isinstance(value, np.bool_):
        return bool(value)

    return value


def robust_add_features(provider, features, label="features"):
    """
    Add a batch to a QGIS provider. If the whole batch is rejected,
    retry one-by-one so a single bad feature does not abort the run.
    """
    if not features:
        return 0, 0

    try:
        ok, _ = provider.addFeatures(features)
        batch_exception = None
    except Exception as exc:
        ok = False
        batch_exception = exc

    if ok:
        return len(features), 0

    print(f"\nWARNING: Batch insertion failed for {label}; retrying individually.")

    if batch_exception is not None:
        print("Batch exception:", batch_exception)

    added = 0
    skipped = 0

    for i, feat in enumerate(features, start=1):
        try:
            ok_one, _ = provider.addFeatures([feat])
        except Exception as exc:
            ok_one = False
            print(f"  {label} {i}: exception {type(exc).__name__}: {exc}")

        if ok_one:
            added += 1
        else:
            skipped += 1
            try:
                geom = feat.geometry()
                geom_type = geom.wkbType() if geom is not None else None
                attrs = feat.attributes()
            except Exception:
                geom_type = None
                attrs = None

            print(
                f"  skipped {label} {i}; geometry type={geom_type}; attrs={attrs}"
            )

    print(f"  added={added}, skipped={skipped}")
    return added, skipped


def safe_num(v):
    if v is None:
        return None

    try:
        if not np.isfinite(v):
            return None
    except Exception:
        return None

    return float(v)


# ================================================================
# LOAD INPUTS
# ================================================================

print("\n====================================================")
print("PMRDA REGIONAL LANDSLIDE RUNOUT SCREENING MODEL")
print("====================================================")

source_layer = get_layer(
    SOURCE_LAYER_NAME
)

dem_layer = get_layer(
    DEM_RASTER_NAME
)

slope_layer = get_layer(
    SLOPE_RASTER_NAME
)

for required_field in (
    "SOURCE_ID",
    "SU_ID"
):
    if (
        source_layer.fields().indexOf(
            required_field
        ) < 0
    ):
        raise RuntimeError(
            f"{SOURCE_LAYER_NAME} is missing "
            f"required field '{required_field}'."
        )

if source_layer.crs().isGeographic():
    raise RuntimeError(
        "Source zones use a geographic CRS. "
        "Runout needs a projected metric CRS."
    )

if dem_layer.crs() != source_layer.crs():
    raise RuntimeError(
        "DEM CRS does not match source-zone CRS."
    )

if slope_layer.crs() != source_layer.crs():
    raise RuntimeError(
        "Slope raster CRS does not match source-zone CRS."
    )

dem_ds = open_raster(
    dem_layer
)

slope_ds = open_raster(
    slope_layer
)

if not same_grid(
    dem_ds,
    slope_ds
):
    raise RuntimeError(
        "dem_filled and slope_deg are not on the same grid."
    )

gt = dem_ds.GetGeoTransform()

dem_band = dem_ds.GetRasterBand(1)
slope_band = slope_ds.GetRasterBand(1)

dem = dem_band.ReadAsArray().astype(
    np.float32
)

slope = slope_band.ReadAsArray().astype(
    np.float32
)

dem_valid = valid_mask(
    dem,
    dem_band
)

slope_valid = valid_mask(
    slope,
    slope_band
)

print("\nScenario:")
print("  reach angle:", REACH_ANGLE_DEG, "degrees")
print(
    "  low-slope stop:",
    LOW_SLOPE_STOP_DEG,
    "degrees for",
    LOW_SLOPE_CONSECUTIVE_CELLS,
    "cells"
)
print(
    "  max runout:",
    MAX_RUNOUT_DISTANCE_M,
    "m"
)
print(
    "  max seeds/source:",
    MAX_SEEDS_PER_SOURCE
)
print(
    "  seed spacing:",
    MIN_SEED_SPACING_M,
    "m"
)
print(
    "  envelope half-width:",
    RUNOUT_HALF_WIDTH_M,
    "m"
)


# ================================================================
# RASTERIZE SOURCE IDs
# ================================================================

source_id_raster_path = os.path.join(
    TEMP_FOLDER,
    "source_ids_dem_grid.tif"
)

delete_output(
    source_id_raster_path
)

print("\nRasterizing SOURCE_ID to DEM grid...")

source_id_ds = rasterize_source_ids(
    dem_ds,
    source_layer,
    source_id_raster_path
)

source_ids = (
    source_id_ds
    .GetRasterBand(1)
    .ReadAsArray()
)

if source_ids is None:
    raise RuntimeError(
        "Could not read SOURCE_ID raster."
    )


# ================================================================
# SOURCE-ID -> SU-ID LOOKUP
# ================================================================

source_to_su = {}

source_geoms = {}

for feat in source_layer.getFeatures():
    source_id = int(
        feat["SOURCE_ID"]
    )
    su_id = int(
        feat["SU_ID"]
    )

    source_to_su[source_id] = su_id

    geom = feat.geometry()

    if geom is not None and not geom.isEmpty():
        source_geoms[source_id] = QgsGeometry(
            geom
        )


# ================================================================
# CREATE PATH MEMORY LAYER
# ================================================================

crs_auth = source_layer.crs().authid()

path_mem = QgsVectorLayer(
    f"LineString?crs={crs_auth}",
    "runout_paths_memory",
    "memory"
)

if not path_mem.isValid():
    raise RuntimeError(
        "Could not create runout path memory layer."
    )

pp = path_mem.dataProvider()

pp.addAttributes([
    QgsField("PATH_ID", QVariant.LongLong),
    QgsField("SOURCE_ID", QVariant.LongLong),
    QgsField("SU_ID", QVariant.LongLong),

    QgsField("START_Z", QVariant.Double, len=20, prec=2),
    QgsField("END_Z", QVariant.Double, len=20, prec=2),
    QgsField("DROP_M", QVariant.Double, len=20, prec=2),
    QgsField("LENGTH_M", QVariant.Double, len=20, prec=2),
    QgsField("REACH_DEG", QVariant.Double, len=20, prec=3),

    QgsField("N_STEPS", QVariant.Int),
    QgsField("END_REASON", QVariant.String, len=30),
])

path_mem.updateFields()


# ================================================================
# TRACE PATHS
# ================================================================

# Provider sanity check: create one tiny valid line with ordinary Python
# scalar attributes, insert it, then delete it. This catches schema/provider
# problems before spending time tracing thousands of sources.
_test_feat = QgsFeature(path_mem.fields())
_test_feat.setGeometry(
    QgsGeometry.fromPolylineXY([
        QgsPointXY(0.0, 0.0),
        QgsPointXY(1.0, 1.0),
    ])
)
_test_feat.setAttributes([
    0, 0, 0,
    0.0, 0.0, 0.0, 1.0, 45.0,
    1, "TEST"
])

_test_ok, _ = pp.addFeatures([_test_feat])

if not _test_ok:
    raise RuntimeError(
        "Runout path memory-layer provider rejected a simple test feature. "
        "This indicates a field/schema/provider problem rather than a "
        "runout-geometry problem."
    )

# Remove the preflight feature again.
for _f in path_mem.getFeatures():
    if int(_f["PATH_ID"]) == 0:
        pp.deleteFeatures([_f.id()])
        break

print("\nIdentifying release seeds and tracing runout...")

unique_source_ids = np.unique(
    source_ids[source_ids > 0]
).astype(int)

path_id = 1
path_batch = []

source_path_stats = {}

for index, source_id in enumerate(
    unique_source_ids,
    start=1
):
    # NumPy iteration yields np.int64. Convert immediately because the
    # QGIS memory provider expects ordinary Python scalar attribute values.
    source_id = int(source_id)
    rows, cols = np.where(
        source_ids == source_id
    )

    if len(rows) == 0:
        continue

    candidates = []

    for row, col in zip(
        rows.tolist(),
        cols.tolist()
    ):
        if not dem_valid[row, col]:
            continue

        if is_downslope_edge_cell(
            row,
            col,
            source_id,
            source_ids,
            dem,
            dem_valid,
            gt
        ):
            candidates.append(
                (
                    row,
                    col,
                    float(dem[row, col])
                )
            )

    seeds = select_spaced_seeds(
        candidates,
        gt,
        MAX_SEEDS_PER_SOURCE,
        MIN_SEED_SPACING_M
    )

    if not seeds:
        continue

    su_id = source_to_su.get(
        source_id,
        0
    )

    stats = {
        "n_paths": 0,
        "max_len": 0.0,
        "max_drop": 0.0,
    }

    for seed_row, seed_col, seed_z in seeds:
        result = trace_path(
            seed_row,
            seed_col,
            dem,
            dem_valid,
            slope,
            slope_valid,
            gt
        )

        if (
            result["n_steps"] < 2
            or len(result["points"]) < 3
        ):
            continue

        # Defensive geometry validation before insertion.
        clean_points = []

        for pt in result["points"]:
            x = float(pt.x())
            y = float(pt.y())

            if not (math.isfinite(x) and math.isfinite(y)):
                continue

            if clean_points:
                prev = clean_points[-1]
                if (
                    abs(prev.x() - x) < 1e-9
                    and abs(prev.y() - y) < 1e-9
                ):
                    continue

            clean_points.append(QgsPointXY(x, y))

        if len(clean_points) < 2:
            continue

        geom = QgsGeometry.fromPolylineXY(clean_points)

        if (
            geom is None
            or geom.isEmpty()
            or geom.length() <= 0
        ):
            continue

        feat = QgsFeature(
            path_mem.fields()
        )

        feat.setGeometry(
            geom
        )

        feat.setAttributes([
            int(path_id),
            int(source_id),
            int(su_id),

            qgis_scalar(
                safe_num(result["start_z"])
            ),
            qgis_scalar(
                safe_num(result["end_z"])
            ),
            qgis_scalar(
                safe_num(result["drop_m"])
            ),
            qgis_scalar(
                safe_num(result["length_m"])
            ),
            qgis_scalar(
                safe_num(result["reach_deg"])
            ),

            int(result["n_steps"]),
            str(result["end_reason"]),
        ])

        path_batch.append(
            feat
        )

        stats["n_paths"] += 1
        stats["max_len"] = max(
            stats["max_len"],
            result["length_m"]
        )
        stats["max_drop"] = max(
            stats["max_drop"],
            result["drop_m"]
        )

        path_id += 1

        if len(path_batch) >= 1000:
            added_now, skipped_now = robust_add_features(
                pp,
                path_batch,
                label="runout path"
            )

            if added_now == 0:
                raise RuntimeError(
                    "The path layer rejected every feature in this batch. "
                    "Use the printed diagnostics above to identify the cause."
                )

            path_batch = []

    if stats["n_paths"] > 0:
        source_path_stats[
            source_id
        ] = stats

    if index % 500 == 0:
        print(
            f"  processed {index}/"
            f"{len(unique_source_ids)} sources"
        )

if path_batch:
    added_now, skipped_now = robust_add_features(
        pp,
        path_batch,
        label="runout path"
    )

    if added_now == 0:
        raise RuntimeError(
            "The path layer rejected every feature in the final batch. "
            "Use the printed diagnostics above to identify the cause."
        )

path_mem.updateExtents()

if path_mem.featureCount() == 0:
    raise RuntimeError(
        "No runout paths were produced."
    )

print(
    "Runout paths generated:",
    path_mem.featureCount()
)


# ================================================================
# SAVE PATHS
# ================================================================

actual_path_gpkg = choose_output(
    PATH_GPKG
)

path_options = (
    QgsVectorFileWriter.SaveVectorOptions()
)

path_options.driverName = "GPKG"
path_options.layerName = PATH_LAYER_NAME
path_options.actionOnExistingFile = (
    QgsVectorFileWriter.CreateOrOverwriteFile
)

result = (
    QgsVectorFileWriter
    .writeAsVectorFormatV3(
        path_mem,
        actual_path_gpkg,
        QgsProject.instance().transformContext(),
        path_options
    )
)

if (
    result[0]
    != QgsVectorFileWriter.NoError
):
    msg = (
        result[1]
        if len(result) > 1
        else ""
    )

    raise RuntimeError(
        "Could not save runout paths.\n"
        f"Error code: {result[0]}\n"
        f"Message: {msg}"
    )

print("\nRunout paths saved:")
print(actual_path_gpkg)


# ================================================================
# BUILD RUNOUT ENVELOPES
# ================================================================

print("\nBuilding per-source runout envelopes...")

envelope_mem = QgsVectorLayer(
    f"MultiPolygon?crs={crs_auth}",
    "runout_envelopes_memory",
    "memory"
)

if not envelope_mem.isValid():
    raise RuntimeError(
        "Could not create runout-envelope memory layer."
    )

ep = envelope_mem.dataProvider()

ep.addAttributes([
    QgsField("SOURCE_ID", QVariant.Int),
    QgsField("SU_ID", QVariant.Int),
    QgsField("N_PATHS", QVariant.Int),
    QgsField("MAX_LEN_M", QVariant.Double, len=20, prec=2),
    QgsField("MAX_DROP_M", QVariant.Double, len=20, prec=2),
    QgsField("AREA_HA", QVariant.Double, len=20, prec=4),
])

envelope_mem.updateFields()

# Collect path geometries by source.
path_geoms = {}

for feat in path_mem.getFeatures():
    source_id = int(
        feat["SOURCE_ID"]
    )

    path_geoms.setdefault(
        source_id,
        []
    ).append(
        QgsGeometry(
            feat.geometry()
        )
    )

envelope_batch = []

for source_id, geoms in path_geoms.items():
    buffered = []

    for geom in geoms:
        try:
            b = geom.buffer(
                RUNOUT_HALF_WIDTH_M,
                8
            )

            if (
                b is not None
                and not b.isEmpty()
            ):
                buffered.append(
                    b
                )
        except Exception:
            continue

    if not buffered:
        continue

    envelope = QgsGeometry.unaryUnion(
        buffered
    )

    if INCLUDE_SOURCE_POLYGON_IN_ENVELOPE:
        source_geom = source_geoms.get(
            source_id
        )

        if (
            source_geom is not None
            and not source_geom.isEmpty()
        ):
            envelope = QgsGeometry.unaryUnion([
                envelope,
                source_geom
            ])

    if envelope is None or envelope.isEmpty():
        continue

    try:
        if not envelope.isGeosValid():
            envelope = envelope.makeValid()
    except Exception:
        pass

    if envelope is None or envelope.isEmpty():
        continue

    # Force multipart representation if needed.
    if not QgsWkbTypes.isMultiType(
        envelope.wkbType()
    ):
        try:
            envelope.convertToMultiType()
        except Exception:
            pass

    stats = source_path_stats.get(
        source_id,
        {}
    )

    su_id = source_to_su.get(
        source_id,
        0
    )

    feat = QgsFeature(
        envelope_mem.fields()
    )

    feat.setGeometry(
        envelope
    )

    feat.setAttributes([
        int(source_id),
        int(su_id),
        int(stats.get("n_paths", len(geoms))),
        qgis_scalar(safe_num(stats.get("max_len", 0.0))),
        qgis_scalar(safe_num(stats.get("max_drop", 0.0))),
        float(envelope.area() / 10000.0),
    ])

    envelope_batch.append(
        feat
    )

    if len(envelope_batch) >= 1000:
        added_now, skipped_now = robust_add_features(
            ep,
            envelope_batch,
            label="runout envelope"
        )

        if added_now == 0:
            raise RuntimeError(
                "The envelope layer rejected every feature in this batch. "
                "Use the printed diagnostics above to identify the cause."
            )

        envelope_batch = []

if envelope_batch:
    added_now, skipped_now = robust_add_features(
        ep,
        envelope_batch,
        label="runout envelope"
    )

    if added_now == 0:
        raise RuntimeError(
            "The envelope layer rejected every feature in the final batch. "
            "Use the printed diagnostics above to identify the cause."
        )

envelope_mem.updateExtents()

if envelope_mem.featureCount() == 0:
    raise RuntimeError(
        "No runout envelopes were generated."
    )

print(
    "Runout envelopes generated:",
    envelope_mem.featureCount()
)


# ================================================================
# SAVE ENVELOPES
# ================================================================

actual_envelope_gpkg = choose_output(
    ENVELOPE_GPKG
)

env_options = (
    QgsVectorFileWriter.SaveVectorOptions()
)

env_options.driverName = "GPKG"
env_options.layerName = ENVELOPE_LAYER_NAME
env_options.actionOnExistingFile = (
    QgsVectorFileWriter.CreateOrOverwriteFile
)

result = (
    QgsVectorFileWriter
    .writeAsVectorFormatV3(
        envelope_mem,
        actual_envelope_gpkg,
        QgsProject.instance().transformContext(),
        env_options
    )
)

if (
    result[0]
    != QgsVectorFileWriter.NoError
):
    msg = (
        result[1]
        if len(result) > 1
        else ""
    )

    raise RuntimeError(
        "Could not save runout envelopes.\n"
        f"Error code: {result[0]}\n"
        f"Message: {msg}"
    )

print("\nRunout envelopes saved:")
print(actual_envelope_gpkg)


# ================================================================
# LOAD OUTPUTS INTO QGIS
# ================================================================

remove_loaded_layers_for_path(
    actual_path_gpkg
)

remove_loaded_layers_for_path(
    actual_envelope_gpkg
)

path_uri = (
    f"{actual_path_gpkg}"
    f"|layername={PATH_LAYER_NAME}"
)

env_uri = (
    f"{actual_envelope_gpkg}"
    f"|layername={ENVELOPE_LAYER_NAME}"
)

path_layer = QgsVectorLayer(
    path_uri,
    PATH_LAYER_NAME,
    "ogr"
)

env_layer = QgsVectorLayer(
    env_uri,
    ENVELOPE_LAYER_NAME,
    "ogr"
)

if not path_layer.isValid():
    raise RuntimeError(
        "Saved runout-path layer could not be reopened."
    )

if not env_layer.isValid():
    raise RuntimeError(
        "Saved runout-envelope layer could not be reopened."
    )

QgsProject.instance().addMapLayer(
    env_layer
)

QgsProject.instance().addMapLayer(
    path_layer
)


# ================================================================
# END-REASON DIAGNOSTICS
# ================================================================

end_reason_counts = {}

for feat in path_layer.getFeatures():
    reason = str(
        feat["END_REASON"]
    )

    end_reason_counts[reason] = (
        end_reason_counts.get(
            reason,
            0
        ) + 1
    )


# ================================================================
# FINAL SUMMARY
# ================================================================

print("\n====================================================")
print("RUNOUT SCREENING COMPLETE")
print("====================================================")

print("\nOutputs:")
print("Paths    :", actual_path_gpkg)
print("Envelopes:", actual_envelope_gpkg)

print("\nCounts:")
print(
    "Source zones represented:",
    len(source_path_stats)
)
print(
    "Runout paths:",
    path_layer.featureCount()
)
print(
    "Runout envelopes:",
    env_layer.featureCount()
)

print("\nPath stopping reasons:")

for reason, count in sorted(
    end_reason_counts.items(),
    key=lambda x: x[0]
):
    print(
        f"  {reason}: {count}"
    )

print(
    "\nNext validation:"
    "\n1. Overlay paths on hillshade/DEM."
    "\n2. Check whether paths stay in plausible downslope corridors."
    "\n3. Inspect runout termini."
    "\n4. Compare at least several reach-angle scenarios "
    "(for example 10°, 12°, 15°)."
    "\n5. Only after this validation intersect envelopes with buildings."
)

print("\nDone.")

# Release GDAL datasets.
source_id_ds = None
dem_ds = None
slope_ds = None



