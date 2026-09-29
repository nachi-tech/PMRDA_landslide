# -*- coding: utf-8 -*-
"""
PMRDA - Candidate Landslide Source Zone Builder
================================================

Purpose
-------
Build plausible landslide release/source zones inside the segmented slope units.

The script does NOT assume that an entire slope unit fails.

Default source rule
-------------------
    slope >= 20 degrees
    AND susceptibility class is 4 or 5
    AND pixel lies inside a slope unit

Then:
    - polygonize contiguous candidate areas
    - keep source areas >= 5,000 m²
    - preserve parent SU_ID
    - calculate source-zone terrain statistics
    - calculate relative vertical position inside the parent slope

Required QGIS layers
--------------------
    slope_units_master
    slope_deg
    aspect_deg
    dem_filled
    08_Landslide_Susceptibility_Index_1_5
    Class_4_High
    Class_5_Very_High

Output
------
Raster:
    candidate_source_pixels.tif

Vector:
    candidate_source_zones.gpkg
    layer: candidate_source_zones

Output fields
-------------
    SOURCE_ID
    SU_ID
    AREA_M2
    AREA_HA

    S_MEAN
    S_P90

    LSI_MEAN

    Z_MIN
    Z_MAX
    Z_MEAN
    RELIEF

    ASP_MEAN
    ASP_CONC

    REL_POS

Interpretation
--------------
REL_POS:
    1.0 = close to upper elevation of the parent slope unit
    0.0 = close to lower elevation of the parent slope unit

ASP_CONC:
    1.0 = highly coherent source orientation
    0.0 = strongly variable orientation

Important
---------
This is a regional screening model. The thresholds below are scenario parameters,
not universal landslide-physics thresholds.

Run this script inside the QGIS Python console/editor.
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
    QgsVectorFileWriter,
    QgsWkbTypes
)
from qgis.PyQt.QtCore import QVariant


# ================================================================
# USER SETTINGS
# ================================================================

SLOPE_UNIT_LAYER_NAME = "slope_units_master"

SLOPE_RASTER_NAME = "slope_deg"
ASPECT_RASTER_NAME = "aspect_deg"
DEM_RASTER_NAME = "dem_filled"
LSI_RASTER_NAME = "08_Landslide_Susceptibility_Index_1_5"

CLASS_4_LAYER_NAME = "Class_4_High"
CLASS_5_LAYER_NAME = "Class_5_Very_High"

# ------------------------------------------------
# Scenario parameters
# ------------------------------------------------

MIN_SOURCE_SLOPE_DEG = 20.0
MIN_SOURCE_AREA_M2 = 5000.0

# If False, rasterization uses pixel centre.
# This is preferable for quantitative area/statistics.
ALL_TOUCHED = False

# 4-connected pixels are more conservative.
# Set True if diagonally touching source cells should become one polygon.
EIGHT_CONNECTED = False

# ------------------------------------------------
# Outputs
# ------------------------------------------------

OUTPUT_FOLDER = r"F:/sim_repos/PMRDA_landslide/data"

SOURCE_MASK_TIF = os.path.join(
    OUTPUT_FOLDER,
    "candidate_source_pixels.tif"
)

OUTPUT_GPKG = os.path.join(
    OUTPUT_FOLDER,
    "candidate_source_zones.gpkg"
)

OUTPUT_LAYER_NAME = "candidate_source_zones"

TEMP_FOLDER = os.path.join(
    OUTPUT_FOLDER,
    "temp_candidate_sources"
)

OVERWRITE_OUTPUT = True

os.makedirs(OUTPUT_FOLDER, exist_ok=True)
os.makedirs(TEMP_FOLDER, exist_ok=True)

gdal.UseExceptions()


# ================================================================
# HELPERS
# ================================================================

def get_layer(name, required=True):
    layers = QgsProject.instance().mapLayersByName(name)

    if not layers:
        if required:
            raise RuntimeError(
                f"Required layer not found in project: {name}"
            )
        return None

    return layers[0]


def raster_path(qgs_raster):
    return qgs_raster.source().split("|")[0]


def open_gdal_raster(qgs_raster):
    path = raster_path(qgs_raster)

    ds = gdal.Open(path, gdal.GA_ReadOnly)

    if ds is None:
        raise RuntimeError(
            f"GDAL could not open raster:\n"
            f"{qgs_raster.name()}\n{path}"
        )

    return ds


def same_grid(ds1, ds2, tol=1e-6):
    if (
        ds1.RasterXSize != ds2.RasterXSize
        or ds1.RasterYSize != ds2.RasterYSize
    ):
        return False

    gt1 = ds1.GetGeoTransform()
    gt2 = ds2.GetGeoTransform()

    for a, b in zip(gt1, gt2):
        if abs(a - b) > tol:
            return False

    s1 = osr.SpatialReference()
    s2 = osr.SpatialReference()

    s1.ImportFromWkt(ds1.GetProjection())
    s2.ImportFromWkt(ds2.GetProjection())

    return bool(s1.IsSame(s2))


def valid_mask(array, band):
    mask = np.isfinite(array)

    nodata = band.GetNoDataValue()

    if nodata is not None:
        if isinstance(nodata, float) and math.isnan(nodata):
            mask &= ~np.isnan(array)
        else:
            mask &= array != nodata

    return mask


def remove_loaded_layers_for_path(path):
    norm = os.path.normcase(os.path.abspath(path))

    for layer in list(QgsProject.instance().mapLayers().values()):
        try:
            src = layer.source().split("|")[0]
            src_norm = os.path.normcase(os.path.abspath(src))

            if src_norm == norm:
                QgsProject.instance().removeMapLayer(layer.id())
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
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fallback = f"{root}_{stamp}{ext}"

    print(
        "\nWARNING: Existing output is locked or overwrite disabled."
        "\nUsing fallback:"
    )
    print(fallback)

    return fallback


def parse_ogr_source(qgs_vector_layer):
    src = qgs_vector_layer.source()
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
            f"OGR could not open vector source:\n{src}"
        )

    if layer_name:
        lyr = ds.GetLayerByName(layer_name)
    else:
        lyr = ds.GetLayer(0)

    if lyr is None:
        raise RuntimeError(
            f"OGR could not open vector layer:\n{src}"
        )

    return ds, lyr


def rasterize_attribute(
    reference_ds,
    qgs_vector_layer,
    attribute_name,
    output_tif,
    gdal_type=gdal.GDT_Int32,
    nodata=0
):
    width = reference_ds.RasterXSize
    height = reference_ds.RasterYSize

    drv = gdal.GetDriverByName("GTiff")

    ds = drv.Create(
        output_tif,
        width,
        height,
        1,
        gdal_type,
        options=[
            "COMPRESS=LZW",
            "TILED=YES",
            "BIGTIFF=IF_SAFER"
        ]
    )

    if ds is None:
        raise RuntimeError(
            f"Could not create raster:\n{output_tif}"
        )

    ds.SetGeoTransform(reference_ds.GetGeoTransform())
    ds.SetProjection(reference_ds.GetProjection())

    band = ds.GetRasterBand(1)
    band.Fill(nodata)
    band.SetNoDataValue(nodata)

    vds, vlyr = parse_ogr_source(qgs_vector_layer)

    options = [f"ATTRIBUTE={attribute_name}"]

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
            f"Rasterization failed for attribute '{attribute_name}'."
        )

    band.FlushCache()
    ds.FlushCache()

    vlyr = None
    vds = None

    return ds


def rasterize_binary_classes(
    reference_ds,
    qgs_layers,
    output_tif
):
    drv = gdal.GetDriverByName("GTiff")

    ds = drv.Create(
        output_tif,
        reference_ds.RasterXSize,
        reference_ds.RasterYSize,
        1,
        gdal.GDT_Byte,
        options=[
            "COMPRESS=LZW",
            "TILED=YES",
            "BIGTIFF=IF_SAFER"
        ]
    )

    if ds is None:
        raise RuntimeError(
            f"Could not create high-susceptibility raster:\n{output_tif}"
        )

    ds.SetGeoTransform(reference_ds.GetGeoTransform())
    ds.SetProjection(reference_ds.GetProjection())

    band = ds.GetRasterBand(1)
    band.Fill(0)
    band.SetNoDataValue(0)

    held = []

    for qgs_layer in qgs_layers:
        vds, vlyr = parse_ogr_source(qgs_layer)
        held.append(vds)

        options = []

        if ALL_TOUCHED:
            options.append("ALL_TOUCHED=TRUE")

        err = gdal.RasterizeLayer(
            ds,
            [1],
            vlyr,
            burn_values=[1],
            options=options
        )

        if err != 0:
            raise RuntimeError(
                f"Rasterizing {qgs_layer.name()} failed."
            )

    band.FlushCache()
    ds.FlushCache()

    held = None

    return ds


def polygonize_candidate_raster(
    candidate_ds,
    output_gpkg,
    layer_name
):
    """
    Polygonize candidate raster where pixel value = parent SU_ID.
    Background value = 0 and is excluded by mask.
    """
    if os.path.exists(output_gpkg):
        delete_file_if_possible(output_gpkg)

    driver = ogr.GetDriverByName("GPKG")

    ods = driver.CreateDataSource(output_gpkg)

    if ods is None:
        raise RuntimeError(
            f"Could not create temporary polygon GeoPackage:\n{output_gpkg}"
        )

    srs = osr.SpatialReference()
    srs.ImportFromWkt(candidate_ds.GetProjection())

    layer = ods.CreateLayer(
        layer_name,
        srs=srs,
        geom_type=ogr.wkbPolygon
    )

    if layer is None:
        raise RuntimeError(
            "Could not create temporary polygon layer."
        )

    fld = ogr.FieldDefn("SU_ID", ogr.OFTInteger)
    layer.CreateField(fld)

    src_band = candidate_ds.GetRasterBand(1)

    # Candidate raster itself is also the mask:
    # zero background is excluded.
    options = []

    if EIGHT_CONNECTED:
        options.append("8CONNECTED=8")

    err = gdal.Polygonize(
        src_band,
        src_band,
        layer,
        0,
        options=options
    )

    if err != 0:
        raise RuntimeError(
            f"GDAL Polygonize failed with code {err}"
        )

    layer.SyncToDisk()

    layer = None
    ods = None


def create_clean_source_layer(
    temp_polygon_path,
    temp_layer_name,
    parent_layer,
    output_gpkg,
    output_layer_name,
    min_area_m2
):
    """
    Read polygonized candidates, remove small polygons and SU_ID=0,
    assign new SOURCE_ID values, and save a clean GeoPackage.
    """
    temp_ds = ogr.Open(temp_polygon_path, 0)

    if temp_ds is None:
        raise RuntimeError(
            f"Could not open temporary candidate polygons:\n"
            f"{temp_polygon_path}"
        )

    temp_lyr = temp_ds.GetLayerByName(temp_layer_name)

    if temp_lyr is None:
        raise RuntimeError(
            "Temporary candidate polygon layer was not found."
        )

    crs_auth = parent_layer.crs().authid()

    mem = QgsVectorLayer(
        f"Polygon?crs={crs_auth}",
        "candidate_sources_clean_memory",
        "memory"
    )

    if not mem.isValid():
        raise RuntimeError(
            "Could not create clean candidate-source memory layer."
        )

    p = mem.dataProvider()

    fields = [
        QgsField("SOURCE_ID", QVariant.Int),
        QgsField("SU_ID", QVariant.Int),

        QgsField("AREA_M2", QVariant.Double, len=20, prec=2),
        QgsField("AREA_HA", QVariant.Double, len=20, prec=4),

        QgsField("S_MEAN", QVariant.Double, len=20, prec=3),
        QgsField("S_P90", QVariant.Double, len=20, prec=3),

        QgsField("LSI_MEAN", QVariant.Double, len=20, prec=4),

        QgsField("Z_MIN", QVariant.Double, len=20, prec=2),
        QgsField("Z_MAX", QVariant.Double, len=20, prec=2),
        QgsField("Z_MEAN", QVariant.Double, len=20, prec=2),
        QgsField("RELIEF", QVariant.Double, len=20, prec=2),

        QgsField("ASP_MEAN", QVariant.Double, len=20, prec=2),
        QgsField("ASP_CONC", QVariant.Double, len=20, prec=4),

        QgsField("REL_POS", QVariant.Double, len=20, prec=4),
    ]

    p.addAttributes(fields)
    mem.updateFields()

    batch = []
    source_id = 1

    print("\nFiltering polygonized candidate source zones...")

    temp_lyr.ResetReading()

    for feat in temp_lyr:
        su_id = feat.GetField("SU_ID")

        if su_id is None or int(su_id) <= 0:
            continue

        geom_ref = feat.GetGeometryRef()

        if geom_ref is None or geom_ref.IsEmpty():
            continue

        area = float(geom_ref.GetArea())

        if area < min_area_m2:
            continue

        qfeat = QgsFeature(mem.fields())

        # QGIS geometry from WKB
        from qgis.core import QgsGeometry
        qgeom = QgsGeometry()
        qgeom.fromWkb(bytes(geom_ref.ExportToWkb()))

        if qgeom.isEmpty():
            continue

        # Make valid if needed.
        if not qgeom.isGeosValid():
            try:
                qgeom = qgeom.makeValid()
            except Exception:
                pass

        if qgeom.isEmpty():
            continue

        # Polygonize can theoretically return multipart after makeValid.
        qfeat.setGeometry(qgeom)

        attrs = [None] * len(mem.fields())
        attrs[0] = source_id
        attrs[1] = int(su_id)
        attrs[2] = qgeom.area()
        attrs[3] = qgeom.area() / 10000.0

        qfeat.setAttributes(attrs)
        batch.append(qfeat)

        source_id += 1

        if len(batch) >= 5000:
            ok, _ = p.addFeatures(batch)

            if not ok:
                raise RuntimeError(
                    "Could not add source polygons to clean layer."
                )

            batch = []

    if batch:
        ok, _ = p.addFeatures(batch)

        if not ok:
            raise RuntimeError(
                "Could not add final source polygon batch."
            )

    mem.updateExtents()

    temp_lyr = None
    temp_ds = None

    if mem.featureCount() == 0:
        raise RuntimeError(
            "No source polygons survived the current thresholds.\n"
            "Consider lowering MIN_SOURCE_SLOPE_DEG or "
            "MIN_SOURCE_AREA_M2."
        )

    print("Candidate source polygons retained:", mem.featureCount())

    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = output_layer_name
    options.actionOnExistingFile = (
        QgsVectorFileWriter.CreateOrOverwriteFile
    )

    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        mem,
        output_gpkg,
        QgsProject.instance().transformContext(),
        options
    )

    if result[0] != QgsVectorFileWriter.NoError:
        msg = result[1] if len(result) > 1 else ""

        raise RuntimeError(
            "Could not write clean candidate-source GeoPackage.\n"
            f"Error code: {result[0]}\n"
            f"Message: {msg}\n"
            f"Target: {output_gpkg}"
        )

    uri = f"{output_gpkg}|layername={output_layer_name}"

    out = QgsVectorLayer(
        uri,
        output_layer_name,
        "ogr"
    )

    if not out.isValid():
        raise RuntimeError(
            "Candidate source GeoPackage was written "
            "but could not be reopened."
        )

    return out


def grouped_mean(zone, values, valid, n):
    z = zone[valid].astype(np.int64, copy=False)
    v = values[valid].astype(np.float64, copy=False)

    counts = np.bincount(
        z,
        minlength=n + 1
    )

    sums = np.bincount(
        z,
        weights=v,
        minlength=n + 1
    )

    out = np.full(
        n + 1,
        np.nan,
        dtype=np.float64
    )

    good = counts > 0

    out[good] = (
        sums[good]
        / counts[good]
    )

    return out, counts


def grouped_min_max(zone, values, valid, n):
    z = zone[valid].astype(np.int64, copy=False)
    v = values[valid].astype(np.float64, copy=False)

    mins = np.full(
        n + 1,
        np.inf,
        dtype=np.float64
    )

    maxs = np.full(
        n + 1,
        -np.inf,
        dtype=np.float64
    )

    np.minimum.at(mins, z, v)
    np.maximum.at(maxs, z, v)

    mins[~np.isfinite(mins)] = np.nan
    maxs[~np.isfinite(maxs)] = np.nan

    return mins, maxs


def grouped_percentile(
    zone,
    values,
    valid,
    n,
    percentile
):
    out = np.full(
        n + 1,
        np.nan,
        dtype=np.float64
    )

    z = zone[valid].astype(
        np.int32,
        copy=False
    )

    v = values[valid].astype(
        np.float32,
        copy=False
    )

    if z.size == 0:
        return out

    order = np.argsort(
        z,
        kind="mergesort"
    )

    z = z[order]
    v = v[order]

    change = np.flatnonzero(
        np.diff(z)
    ) + 1

    starts = np.r_[0, change]
    ends = np.r_[change, len(z)]

    for start, end in zip(starts, ends):
        zid = int(z[start])

        if zid <= 0 or zid > n:
            continue

        out[zid] = float(
            np.percentile(
                v[start:end],
                percentile
            )
        )

    return out


def circular_stats(
    zone,
    aspect_deg,
    valid,
    n
):
    z = zone[valid].astype(
        np.int64,
        copy=False
    )

    angles = np.deg2rad(
        aspect_deg[valid].astype(
            np.float64,
            copy=False
        )
    )

    counts = np.bincount(
        z,
        minlength=n + 1
    )

    sin_sum = np.bincount(
        z,
        weights=np.sin(angles),
        minlength=n + 1
    )

    cos_sum = np.bincount(
        z,
        weights=np.cos(angles),
        minlength=n + 1
    )

    mean_deg = np.full(
        n + 1,
        np.nan,
        dtype=np.float64
    )

    conc = np.full(
        n + 1,
        np.nan,
        dtype=np.float64
    )

    good = counts > 0

    mean_deg[good] = (
        np.degrees(
            np.arctan2(
                sin_sum[good],
                cos_sum[good]
            )
        )
        + 360.0
    ) % 360.0

    conc[good] = (
        np.sqrt(
            sin_sum[good] ** 2
            + cos_sum[good] ** 2
        )
        / counts[good]
    )

    return mean_deg, conc, counts


def safe_float(x):
    try:
        if x is None or not np.isfinite(x):
            return None
    except Exception:
        return None

    return float(x)


# ================================================================
# LOAD INPUTS
# ================================================================

print("\n====================================================")
print("PMRDA CANDIDATE LANDSLIDE SOURCE-ZONE BUILDER")
print("====================================================")

slope_units = get_layer(
    SLOPE_UNIT_LAYER_NAME
)

slope_raster = get_layer(
    SLOPE_RASTER_NAME
)

aspect_raster = get_layer(
    ASPECT_RASTER_NAME
)

dem_raster = get_layer(
    DEM_RASTER_NAME
)

lsi_raster = get_layer(
    LSI_RASTER_NAME
)

class4 = get_layer(
    CLASS_4_LAYER_NAME
)

class5 = get_layer(
    CLASS_5_LAYER_NAME
)

print("\nScenario:")
print(
    "  minimum source slope:",
    MIN_SOURCE_SLOPE_DEG,
    "degrees"
)
print(
    "  minimum source area:",
    MIN_SOURCE_AREA_M2,
    "m2"
)
print(
    "  susceptibility classes: 4 + 5"
)
print(
    "  diagonal connectivity:",
    "8-connected" if EIGHT_CONNECTED else "4-connected"
)


# ================================================================
# CRS / GRID CHECKS
# ================================================================

if slope_units.crs().isGeographic():
    raise RuntimeError(
        "slope_units_master is in a geographic CRS. "
        "A projected metric CRS is required."
    )

for raster in [
    slope_raster,
    aspect_raster,
    dem_raster,
    lsi_raster,
]:
    if raster.crs() != slope_units.crs():
        raise RuntimeError(
            f"CRS mismatch: {raster.name()} is "
            f"{raster.crs().authid()}, while slope units are "
            f"{slope_units.crs().authid()}."
        )

slope_ds = open_gdal_raster(
    slope_raster
)

aspect_ds = open_gdal_raster(
    aspect_raster
)

dem_ds = open_gdal_raster(
    dem_raster
)

lsi_ds = open_gdal_raster(
    lsi_raster
)

if not same_grid(slope_ds, aspect_ds):
    raise RuntimeError(
        "slope_deg and aspect_deg are not aligned."
    )

if not same_grid(slope_ds, dem_ds):
    raise RuntimeError(
        "slope_deg and dem_filled are not aligned."
    )

# The LSI raster may be on a different resolution/grid.
# We use it later for source statistics on its own grid.


# ================================================================
# RASTERIZE PARENT SLOPE UNITS
# ================================================================

su_raster_path = os.path.join(
    TEMP_FOLDER,
    "parent_su_id_terrain.tif"
)

delete_file_if_possible(
    su_raster_path
)

print("\nRasterizing parent slope-unit IDs...")

su_ds = rasterize_attribute(
    slope_ds,
    slope_units,
    "SU_ID",
    su_raster_path,
    gdal.GDT_Int32,
    0
)

su_arr = su_ds.GetRasterBand(
    1
).ReadAsArray()

if su_arr is None:
    raise RuntimeError(
        "Could not read rasterized SU_ID grid."
    )


# ================================================================
# RASTERIZE HIGH SUSCEPTIBILITY (CLASS 4 + 5)
# ================================================================

high_class_path = os.path.join(
    TEMP_FOLDER,
    "high_susceptibility_4_5.tif"
)

delete_file_if_possible(
    high_class_path
)

print(
    "Rasterizing Class 4 + Class 5 "
    "onto terrain grid..."
)

high_ds = rasterize_binary_classes(
    slope_ds,
    [class4, class5],
    high_class_path
)

high_arr = high_ds.GetRasterBand(
    1
).ReadAsArray()


# ================================================================
# BUILD CANDIDATE SOURCE RASTER
# ================================================================

print("\nBuilding candidate source-pixel mask...")

slope_band = slope_ds.GetRasterBand(1)
slope_arr = slope_band.ReadAsArray().astype(
    np.float32
)

slope_ok = (
    valid_mask(
        slope_arr,
        slope_band
    )
    & (
        slope_arr
        >= MIN_SOURCE_SLOPE_DEG
    )
)

candidate = (
    (su_arr > 0)
    & (high_arr == 1)
    & slope_ok
)

candidate_count = int(
    np.count_nonzero(candidate)
)

if candidate_count == 0:
    raise RuntimeError(
        "No candidate source pixels were found with the "
        "current slope/class thresholds."
    )

candidate_su = np.where(
    candidate,
    su_arr,
    0
).astype(np.int32)

gt = slope_ds.GetGeoTransform()

pixel_area = abs(
    gt[1] * gt[5]
    - gt[2] * gt[4]
)

print("Candidate pixels:", candidate_count)
print(
    "Approx candidate area:",
    round(
        candidate_count
        * pixel_area
        / 1_000_000.0,
        3
    ),
    "km2"
)

actual_mask_path = choose_output_path(
    SOURCE_MASK_TIF
)

mask_drv = gdal.GetDriverByName(
    "GTiff"
)

candidate_ds = mask_drv.Create(
    actual_mask_path,
    slope_ds.RasterXSize,
    slope_ds.RasterYSize,
    1,
    gdal.GDT_Int32,
    options=[
        "COMPRESS=LZW",
        "TILED=YES",
        "BIGTIFF=IF_SAFER"
    ]
)

if candidate_ds is None:
    raise RuntimeError(
        f"Could not create candidate raster:\n"
        f"{actual_mask_path}"
    )

candidate_ds.SetGeoTransform(
    slope_ds.GetGeoTransform()
)

candidate_ds.SetProjection(
    slope_ds.GetProjection()
)

candidate_band = candidate_ds.GetRasterBand(
    1
)

candidate_band.WriteArray(
    candidate_su
)

candidate_band.SetNoDataValue(
    0
)

candidate_band.FlushCache()
candidate_ds.FlushCache()

print(
    "Candidate raster written:"
)
print(actual_mask_path)


# ================================================================
# POLYGONIZE
# ================================================================

temp_poly_gpkg = os.path.join(
    TEMP_FOLDER,
    "candidate_sources_polygonized.gpkg"
)

delete_file_if_possible(
    temp_poly_gpkg
)

print("\nPolygonizing candidate source pixels...")

polygonize_candidate_raster(
    candidate_ds,
    temp_poly_gpkg,
    "candidate_sources_raw"
)

actual_output_gpkg = choose_output_path(
    OUTPUT_GPKG
)

source_layer = create_clean_source_layer(
    temp_poly_gpkg,
    "candidate_sources_raw",
    slope_units,
    actual_output_gpkg,
    OUTPUT_LAYER_NAME,
    MIN_SOURCE_AREA_M2
)

n_sources = source_layer.featureCount()

print("\nSource polygons after area filter:", n_sources)


# ================================================================
# RASTERIZE SOURCE_ID TO TERRAIN GRID
# ================================================================

source_id_terrain_path = os.path.join(
    TEMP_FOLDER,
    "source_id_terrain.tif"
)

delete_file_if_possible(
    source_id_terrain_path
)

source_id_ds = rasterize_attribute(
    slope_ds,
    source_layer,
    "SOURCE_ID",
    source_id_terrain_path,
    gdal.GDT_Int32,
    0
)

source_zone = source_id_ds.GetRasterBand(
    1
).ReadAsArray()

if source_zone is None:
    raise RuntimeError(
        "Could not read SOURCE_ID terrain raster."
    )


# ================================================================
# TERRAIN STATS PER SOURCE ZONE
# ================================================================

print("\nCalculating source-zone terrain statistics...")

aspect_band = aspect_ds.GetRasterBand(1)
dem_band = dem_ds.GetRasterBand(1)

aspect_arr = aspect_band.ReadAsArray().astype(
    np.float32
)

dem_arr = dem_band.ReadAsArray().astype(
    np.float32
)

inside_source = source_zone > 0

valid_slope = (
    inside_source
    & valid_mask(
        slope_arr,
        slope_band
    )
)

valid_aspect = (
    inside_source
    & valid_mask(
        aspect_arr,
        aspect_band
    )
    & (aspect_arr >= 0.0)
    & (aspect_arr <= 360.0)
)

valid_dem = (
    inside_source
    & valid_mask(
        dem_arr,
        dem_band
    )
)

s_mean, slope_counts = grouped_mean(
    source_zone,
    slope_arr,
    valid_slope,
    n_sources
)

s_p90 = grouped_percentile(
    source_zone,
    slope_arr,
    valid_slope,
    n_sources,
    90
)

z_mean, dem_counts = grouped_mean(
    source_zone,
    dem_arr,
    valid_dem,
    n_sources
)

z_min, z_max = grouped_min_max(
    source_zone,
    dem_arr,
    valid_dem,
    n_sources
)

source_relief = (
    z_max - z_min
)

asp_mean, asp_conc, asp_counts = circular_stats(
    source_zone,
    aspect_arr,
    valid_aspect,
    n_sources
)


# ================================================================
# LSI MEAN PER SOURCE ZONE
# ================================================================

print("Calculating mean LSI per source zone...")

source_id_lsi_path = os.path.join(
    TEMP_FOLDER,
    "source_id_lsi.tif"
)

delete_file_if_possible(
    source_id_lsi_path
)

source_id_lsi_ds = rasterize_attribute(
    lsi_ds,
    source_layer,
    "SOURCE_ID",
    source_id_lsi_path,
    gdal.GDT_Int32,
    0
)

source_zone_lsi = source_id_lsi_ds.GetRasterBand(
    1
).ReadAsArray()

lsi_band = lsi_ds.GetRasterBand(1)

lsi_arr = lsi_band.ReadAsArray().astype(
    np.float32
)

valid_lsi = (
    (source_zone_lsi > 0)
    & valid_mask(
        lsi_arr,
        lsi_band
    )
)

lsi_mean, lsi_counts = grouped_mean(
    source_zone_lsi,
    lsi_arr,
    valid_lsi,
    n_sources
)


# ================================================================
# PARENT SLOPE ELEVATION LOOKUP
# ================================================================

print(
    "Calculating source relative vertical position "
    "inside parent slope..."
)

parent_z = {}

if (
    slope_units.fields().indexOf("Z_MIN") < 0
    or slope_units.fields().indexOf("Z_MAX") < 0
):
    raise RuntimeError(
        "slope_units_master must contain Z_MIN and Z_MAX."
    )

for feat in slope_units.getFeatures():
    try:
        su_id = int(feat["SU_ID"])
    except Exception:
        continue

    pzmin = feat["Z_MIN"]
    pzmax = feat["Z_MAX"]

    try:
        pzmin = float(pzmin)
        pzmax = float(pzmax)
    except Exception:
        continue

    if (
        np.isfinite(pzmin)
        and np.isfinite(pzmax)
    ):
        parent_z[su_id] = (
            pzmin,
            pzmax
        )


# ================================================================
# WRITE STATISTICS
# ================================================================

print("\nWriting source-zone attributes...")

idx = {
    name: source_layer.fields().indexOf(
        name
    )
    for name in [
        "SOURCE_ID",
        "SU_ID",
        "S_MEAN",
        "S_P90",
        "LSI_MEAN",
        "Z_MIN",
        "Z_MAX",
        "Z_MEAN",
        "RELIEF",
        "ASP_MEAN",
        "ASP_CONC",
        "REL_POS",
    ]
}

for name, field_idx in idx.items():
    if field_idx < 0:
        raise RuntimeError(
            f"Missing expected field: {name}"
        )

changes = {}

for feat in source_layer.getFeatures():
    source_id = int(
        feat["SOURCE_ID"]
    )

    su_id = int(
        feat["SU_ID"]
    )

    rel_pos = None

    if (
        su_id in parent_z
        and np.isfinite(
            z_mean[source_id]
        )
    ):
        parent_min, parent_max = (
            parent_z[su_id]
        )

        denom = (
            parent_max - parent_min
        )

        if denom > 0:
            rel_pos = (
                z_mean[source_id]
                - parent_min
            ) / denom

            # Clamp tiny raster/geometry edge differences.
            rel_pos = max(
                0.0,
                min(
                    1.0,
                    rel_pos
                )
            )

    values = {
        idx["S_MEAN"]: safe_float(
            s_mean[source_id]
        ),
        idx["S_P90"]: safe_float(
            s_p90[source_id]
        ),

        idx["LSI_MEAN"]: safe_float(
            lsi_mean[source_id]
        ),

        idx["Z_MIN"]: safe_float(
            z_min[source_id]
        ),
        idx["Z_MAX"]: safe_float(
            z_max[source_id]
        ),
        idx["Z_MEAN"]: safe_float(
            z_mean[source_id]
        ),
        idx["RELIEF"]: safe_float(
            source_relief[source_id]
        ),

        idx["ASP_MEAN"]: safe_float(
            asp_mean[source_id]
        ),
        idx["ASP_CONC"]: safe_float(
            asp_conc[source_id]
        ),

        idx["REL_POS"]: (
            float(rel_pos)
            if rel_pos is not None
            else None
        ),
    }

    changes[feat.id()] = values

    if len(changes) >= 5000:
        ok = (
            source_layer
            .dataProvider()
            .changeAttributeValues(
                changes
            )
        )

        if not ok:
            raise RuntimeError(
                "Failed writing source-zone attributes."
            )

        changes = {}

if changes:
    ok = (
        source_layer
        .dataProvider()
        .changeAttributeValues(
            changes
        )
    )

    if not ok:
        raise RuntimeError(
            "Failed writing final source-zone attribute batch."
        )

source_layer.triggerRepaint()


# ================================================================
# REOPEN FINAL VECTOR + ADD OUTPUTS TO QGIS
# ================================================================

remove_loaded_layers_for_path(
    actual_output_gpkg
)

source_layer = None

final_uri = (
    f"{actual_output_gpkg}"
    f"|layername={OUTPUT_LAYER_NAME}"
)

final_source_layer = QgsVectorLayer(
    final_uri,
    OUTPUT_LAYER_NAME,
    "ogr"
)

if not final_source_layer.isValid():
    raise RuntimeError(
        "Final source-zone layer could not be reopened."
    )

QgsProject.instance().addMapLayer(
    final_source_layer
)

candidate_raster_qgis = QgsRasterLayer(
    actual_mask_path,
    "candidate_source_pixels"
)

if candidate_raster_qgis.isValid():
    QgsProject.instance().addMapLayer(
        candidate_raster_qgis
    )


# ================================================================
# DIAGNOSTICS
# ================================================================

print("\n====================================================")
print("CANDIDATE SOURCE-ZONE MODEL COMPLETE")
print("====================================================")

print("\nOutputs:")
print(
    "Raster:",
    actual_mask_path
)
print(
    "Vector:",
    actual_output_gpkg
)
print(
    "Layer :",
    OUTPUT_LAYER_NAME
)

print("\nCounts:")
print(
    "Candidate source pixels:",
    candidate_count
)
print(
    "Retained source polygons:",
    final_source_layer.featureCount()
)

no_slope = int(
    np.sum(
        slope_counts[1:] == 0
    )
)

no_lsi = int(
    np.sum(
        lsi_counts[1:] == 0
    )
)

no_aspect = int(
    np.sum(
        asp_counts[1:] == 0
    )
)

print("\nCoverage diagnostics:")
print(
    "Sources without slope pixels:",
    no_slope
)
print(
    "Sources without aspect pixels:",
    no_aspect
)
print(
    "Sources without LSI pixels:",
    no_lsi
)

print("\nSuggested first inspection:")
print(
    "1. Symbolize source zones by REL_POS."
)
print(
    "2. Inspect source zones over hillshade + susceptibility."
)
print(
    "3. Check whether large low-slope/valley-bottom patches remain."
)
print(
    "4. Compare 20° / 25° slope thresholds if necessary."
)
print(
    "5. Do NOT proceed to runout until several terrain types "
    "have been visually checked."
)

print("\nDone.")

# Explicitly release GDAL datasets.
su_ds = None
high_ds = None
candidate_ds = None
source_id_ds = None
source_id_lsi_ds = None

slope_ds = None
aspect_ds = None
dem_ds = None
lsi_ds = None



