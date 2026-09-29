# -*- coding: utf-8 -*-
"""
PMRDA - Build master slope-unit attribute dataset

Run inside the QGIS Python console/editor.

Required QGIS layers:
    slope_faces_final
    slope_deg
    aspect_deg
    dem_filled
    08_Landslide_Susceptibility_Index_1_5

Optional:
    curvature_total
    Class_1_Very_Low ... Class_5_Very_High

Creates:
    SU_ID, AREA_HA,
    S_MEAN, S_P75, S_P90, S_MAX,
    ASP_MEAN, ASP_CONC,
    Z_MIN, Z_MAX, RELIEF,
    CURV_MEAN,
    MEAN_LSI,
    PCT_C1 ... PCT_C5, HIGH_PCT, DOM_CLASS

Design goals:
- no Processing-based GeoPackage writer
- inherited fid/ogc_fid fields are dropped
- fresh unique SU_ID values are generated
- aspect mean is circular
- slope percentiles are exact raster-pixel percentiles
- class percentages come from the actual Class_1..Class_5 polygon layers
- if the requested output is locked, a timestamped fallback file is used
"""

import os
from datetime import datetime

import numpy as np
from osgeo import gdal, ogr, osr

from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsField,
    QgsFeature,
    QgsVectorFileWriter,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QVariant


# ================================================================
# USER SETTINGS
# ================================================================

SLOPE_UNIT_LAYER_NAME = "slope_faces_final"
SLOPE_RASTER_NAME = "slope_deg"
ASPECT_RASTER_NAME = "aspect_deg"
DEM_RASTER_NAME = "dem_filled"
CURVATURE_RASTER_NAME = "curvature_total"
LSI_RASTER_NAME = "08_Landslide_Susceptibility_Index_1_5"

CLASS_LAYER_NAMES = {
    1: "Class_1_Very_Low",
    2: "Class_2_Low",
    3: "Class_3_Moderate",
    4: "Class_4_High",
    5: "Class_5_Very_High",
}

OUTPUT_GPKG = r"F:/sim_repos/PMRDA_landslide/data/slope_units_master.gpkg"
OUTPUT_LAYER_NAME = "slope_units_master"
TEMP_FOLDER = r"F:/sim_repos/PMRDA_landslide/data/temp_slope_master"

ALL_TOUCHED = False
CALCULATE_SLOPE_PERCENTILES = True
OVERWRITE_OUTPUT = True

os.makedirs(TEMP_FOLDER, exist_ok=True)
os.makedirs(os.path.dirname(OUTPUT_GPKG), exist_ok=True)
gdal.UseExceptions()


# ================================================================
# HELPERS
# ================================================================

def get_layer(name, required=True):
    layers = QgsProject.instance().mapLayersByName(name)
    if not layers:
        if required:
            raise RuntimeError(f"Required layer not found: {name}")
        return None
    return layers[0]


def raster_path(layer):
    return layer.source().split("|")[0]


def open_gdal_raster(layer):
    path = raster_path(layer)
    ds = gdal.Open(path, gdal.GA_ReadOnly)
    if ds is None:
        raise RuntimeError(f"GDAL could not open raster {layer.name()}:\n{path}")
    return ds


def same_grid(a, b, tol=1e-6):
    if a.RasterXSize != b.RasterXSize or a.RasterYSize != b.RasterYSize:
        return False
    gta = a.GetGeoTransform()
    gtb = b.GetGeoTransform()
    if any(abs(x - y) > tol for x, y in zip(gta, gtb)):
        return False
    sa = osr.SpatialReference()
    sb = osr.SpatialReference()
    sa.ImportFromWkt(a.GetProjection())
    sb.ImportFromWkt(b.GetProjection())
    return bool(sa.IsSame(sb))


def valid_mask(array, band):
    mask = np.isfinite(array)
    nodata = band.GetNoDataValue()
    if nodata is not None:
        if isinstance(nodata, float) and np.isnan(nodata):
            mask &= ~np.isnan(array)
        else:
            mask &= array != nodata
    return mask


def remove_loaded_output_layers(path, layer_name):
    target = os.path.normcase(os.path.abspath(path))
    for lyr in list(QgsProject.instance().mapLayers().values()):
        try:
            src = os.path.normcase(os.path.abspath(lyr.source().split("|")[0]))
            if lyr.name() == layer_name or src == target:
                QgsProject.instance().removeMapLayer(lyr.id())
        except Exception:
            pass


def choose_output_path(path):
    if not os.path.exists(path):
        return path
    if not OVERWRITE_OUTPUT:
        root, ext = os.path.splitext(path)
        return f"{root}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"

    remove_loaded_output_layers(path, OUTPUT_LAYER_NAME)
    for suffix in ("-wal", "-shm"):
        p = path + suffix
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
    try:
        os.remove(path)
        return path
    except Exception:
        root, ext = os.path.splitext(path)
        fallback = f"{root}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
        print("WARNING: output is locked; using fallback:")
        print(fallback)
        return fallback


def create_clean_output(source_layer, output_path, output_layer_name):
    geom_name = QgsWkbTypes.displayString(source_layer.wkbType())
    mem = QgsVectorLayer(
        f"{geom_name}?crs={source_layer.crs().authid()}",
        "slope_units_clean_memory",
        "memory",
    )
    if not mem.isValid():
        raise RuntimeError("Could not create clean memory layer.")

    provider = mem.dataProvider()

    regenerated = {
        "SU_ID", "AREA_HA",
        "S_MEAN", "S_P75", "S_P90", "S_MAX",
        "ASP_MEAN", "ASP_CONC",
        "Z_MIN", "Z_MAX", "RELIEF", "CURV_MEAN",
        "MEAN_LSI",
        "PCT_C1", "PCT_C2", "PCT_C3", "PCT_C4", "PCT_C5",
        "HIGH_PCT", "DOM_CLASS",
    }

    source_fields = []
    source_indices = []
    for i, fld in enumerate(source_layer.fields()):
        if fld.name().lower() in {"fid", "ogc_fid"}:
            continue
        if fld.name() in regenerated:
            continue
        source_fields.append(fld)
        source_indices.append(i)

    provider.addAttributes(source_fields)
    provider.addAttributes([
        QgsField("SU_ID", QVariant.Int),
        QgsField("AREA_HA", QVariant.Double, len=20, prec=4),
        QgsField("S_MEAN", QVariant.Double, len=20, prec=3),
        QgsField("S_P75", QVariant.Double, len=20, prec=3),
        QgsField("S_P90", QVariant.Double, len=20, prec=3),
        QgsField("S_MAX", QVariant.Double, len=20, prec=3),
        QgsField("ASP_MEAN", QVariant.Double, len=20, prec=2),
        QgsField("ASP_CONC", QVariant.Double, len=20, prec=4),
        QgsField("Z_MIN", QVariant.Double, len=20, prec=2),
        QgsField("Z_MAX", QVariant.Double, len=20, prec=2),
        QgsField("RELIEF", QVariant.Double, len=20, prec=2),
        QgsField("CURV_MEAN", QVariant.Double, len=20, prec=6),
        QgsField("MEAN_LSI", QVariant.Double, len=20, prec=4),
        QgsField("PCT_C1", QVariant.Double, len=20, prec=3),
        QgsField("PCT_C2", QVariant.Double, len=20, prec=3),
        QgsField("PCT_C3", QVariant.Double, len=20, prec=3),
        QgsField("PCT_C4", QVariant.Double, len=20, prec=3),
        QgsField("PCT_C5", QVariant.Double, len=20, prec=3),
        QgsField("HIGH_PCT", QVariant.Double, len=20, prec=3),
        QgsField("DOM_CLASS", QVariant.Int),
    ])
    mem.updateFields()

    idx_su = mem.fields().indexOf("SU_ID")
    idx_area = mem.fields().indexOf("AREA_HA")

    batch = []
    su = 1
    for src_feat in source_layer.getFeatures():
        geom = src_feat.geometry()
        if geom is None or geom.isEmpty():
            continue
        f = QgsFeature(mem.fields())
        f.setGeometry(geom)
        attrs = [None] * len(mem.fields())
        for out_i, src_i in enumerate(source_indices):
            attrs[out_i] = src_feat[src_i]
        attrs[idx_su] = su
        attrs[idx_area] = geom.area() / 10000.0
        f.setAttributes(attrs)
        batch.append(f)
        su += 1
        if len(batch) >= 5000:
            ok, _ = provider.addFeatures(batch)
            if not ok:
                raise RuntimeError("Failed copying slope-unit features.")
            batch = []
    if batch:
        ok, _ = provider.addFeatures(batch)
        if not ok:
            raise RuntimeError("Failed copying final slope-unit batch.")

    mem.updateExtents()
    print("Clean features:", mem.featureCount())

    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "GPKG"
    opts.layerName = output_layer_name
    opts.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile

    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        mem,
        output_path,
        QgsProject.instance().transformContext(),
        opts,
    )
    if result[0] != QgsVectorFileWriter.NoError:
        msg = result[1] if len(result) > 1 else ""
        raise RuntimeError(
            f"Could not create master GeoPackage.\nCode: {result[0]}\nMessage: {msg}"
        )

    out = QgsVectorLayer(
        f"{output_path}|layername={output_layer_name}",
        output_layer_name,
        "ogr",
    )
    if not out.isValid():
        raise RuntimeError("Master GeoPackage was written but could not be reopened.")
    return out


def create_zone_raster(reference_ds, vector_path, vector_layer_name, out_tif):
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(
        out_tif,
        reference_ds.RasterXSize,
        reference_ds.RasterYSize,
        1,
        gdal.GDT_Int32,
        options=["COMPRESS=LZW", "TILED=YES", "BIGTIFF=IF_SAFER"],
    )
    if ds is None:
        raise RuntimeError(f"Could not create zone raster: {out_tif}")
    ds.SetGeoTransform(reference_ds.GetGeoTransform())
    ds.SetProjection(reference_ds.GetProjection())
    band = ds.GetRasterBand(1)
    band.Fill(0)
    band.SetNoDataValue(0)

    vds = ogr.Open(vector_path, 0)
    if vds is None:
        raise RuntimeError(f"Could not open vector source: {vector_path}")
    vlyr = vds.GetLayerByName(vector_layer_name)
    if vlyr is None:
        raise RuntimeError(f"Layer {vector_layer_name} not found in {vector_path}")

    options = ["ATTRIBUTE=SU_ID"]
    if ALL_TOUCHED:
        options.append("ALL_TOUCHED=TRUE")
    err = gdal.RasterizeLayer(ds, [1], vlyr, options=options)
    if err != 0:
        raise RuntimeError(f"Slope-unit rasterization failed with GDAL code {err}")
    band.FlushCache()
    ds.FlushCache()
    vlyr = None
    vds = None
    return ds


def bincount_mean(zone, values, valid, n):
    z = zone[valid].astype(np.int64, copy=False)
    v = values[valid].astype(np.float64, copy=False)
    count = np.bincount(z, minlength=n + 1)
    sums = np.bincount(z, weights=v, minlength=n + 1)
    out = np.full(n + 1, np.nan, dtype=np.float64)
    good = count > 0
    out[good] = sums[good] / count[good]
    return out, count


def grouped_min_max(zone, values, valid, n):
    z = zone[valid].astype(np.int64, copy=False)
    v = values[valid].astype(np.float64, copy=False)
    mins = np.full(n + 1, np.inf)
    maxs = np.full(n + 1, -np.inf)
    np.minimum.at(mins, z, v)
    np.maximum.at(maxs, z, v)
    mins[~np.isfinite(mins)] = np.nan
    maxs[~np.isfinite(maxs)] = np.nan
    return mins, maxs


def grouped_percentiles(zone, values, valid, n, percentiles=(75, 90)):
    outputs = {p: np.full(n + 1, np.nan) for p in percentiles}
    z = zone[valid].astype(np.int32, copy=False)
    v = values[valid].astype(np.float32, copy=False)
    if z.size == 0:
        return outputs
    order = np.argsort(z, kind="mergesort")
    z = z[order]
    v = v[order]
    cuts = np.flatnonzero(np.diff(z)) + 1
    starts = np.r_[0, cuts]
    ends = np.r_[cuts, len(z)]
    for start, end in zip(starts, ends):
        zid = int(z[start])
        if zid <= 0 or zid > n:
            continue
        vals = v[start:end]
        for p in percentiles:
            outputs[p][zid] = float(np.percentile(vals, p))
    return outputs


def circular_aspect(zone, aspect, valid, n):
    z = zone[valid].astype(np.int64, copy=False)
    radians = np.deg2rad(aspect[valid].astype(np.float64, copy=False))
    sin_sum = np.bincount(z, weights=np.sin(radians), minlength=n + 1)
    cos_sum = np.bincount(z, weights=np.cos(radians), minlength=n + 1)
    count = np.bincount(z, minlength=n + 1)
    mean = np.full(n + 1, np.nan)
    conc = np.full(n + 1, np.nan)
    good = count > 0
    mean[good] = (np.degrees(np.arctan2(sin_sum[good], cos_sum[good])) + 360.0) % 360.0
    conc[good] = np.sqrt(sin_sum[good] ** 2 + cos_sum[good] ** 2) / count[good]
    return mean, conc, count


def parse_ogr_source(qgs_layer):
    parts = qgs_layer.source().split("|")
    path = parts[0]
    layer_name = None
    for p in parts[1:]:
        if p.lower().startswith("layername="):
            layer_name = p.split("=", 1)[1]
            break
    ds = ogr.Open(path, 0)
    if ds is None:
        raise RuntimeError(f"OGR could not open: {qgs_layer.source()}")
    lyr = ds.GetLayerByName(layer_name) if layer_name else ds.GetLayer(0)
    if lyr is None:
        raise RuntimeError(f"OGR could not resolve layer: {qgs_layer.source()}")
    return ds, lyr


def rasterize_classes(reference_ds, class_layers, out_tif):
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(
        out_tif,
        reference_ds.RasterXSize,
        reference_ds.RasterYSize,
        1,
        gdal.GDT_Byte,
        options=["COMPRESS=LZW", "TILED=YES", "BIGTIFF=IF_SAFER"],
    )
    if ds is None:
        raise RuntimeError(f"Could not create class raster: {out_tif}")
    ds.SetGeoTransform(reference_ds.GetGeoTransform())
    ds.SetProjection(reference_ds.GetProjection())
    band = ds.GetRasterBand(1)
    band.Fill(0)
    band.SetNoDataValue(0)

    held = []
    for class_id in range(1, 6):
        ods, olyr = parse_ogr_source(class_layers[class_id])
        held.append(ods)
        opts = ["ALL_TOUCHED=TRUE"] if ALL_TOUCHED else []
        err = gdal.RasterizeLayer(ds, [1], olyr, burn_values=[class_id], options=opts)
        if err != 0:
            raise RuntimeError(f"Rasterizing class {class_id} failed.")
    band.FlushCache()
    ds.FlushCache()
    return ds, held


def safe_float(x):
    try:
        return float(x) if np.isfinite(x) else None
    except Exception:
        return None


# ================================================================
# LOAD INPUTS
# ================================================================

print("\n==============================================")
print("PMRDA SLOPE-UNIT MASTER ATTRIBUTE BUILDER")
print("==============================================")

slope_units = get_layer(SLOPE_UNIT_LAYER_NAME)
slope_r = get_layer(SLOPE_RASTER_NAME)
aspect_r = get_layer(ASPECT_RASTER_NAME)
dem_r = get_layer(DEM_RASTER_NAME)
lsi_r = get_layer(LSI_RASTER_NAME)
curv_r = get_layer(CURVATURE_RASTER_NAME, required=False)
class_layers = {i: get_layer(name, required=False) for i, name in CLASS_LAYER_NAMES.items()}

print("Inputs found:")
print("  slope units:", slope_units.name())
print("  slope      :", slope_r.name())
print("  aspect     :", aspect_r.name())
print("  DEM        :", dem_r.name())
print("  LSI        :", lsi_r.name())
print("  curvature  :", curv_r.name() if curv_r else "NOT FOUND")
for i in range(1, 6):
    print(f"  class {i}    :", class_layers[i].name() if class_layers[i] else "NOT FOUND")

if not slope_units.crs().isValid() or slope_units.crs().isGeographic():
    raise RuntimeError("Slope units must use a valid projected CRS.")

for ras in (slope_r, aspect_r, dem_r, lsi_r):
    if ras.crs() != slope_units.crs():
        raise RuntimeError(
            f"CRS mismatch: {ras.name()}={ras.crs().authid()} vs slope units={slope_units.crs().authid()}"
        )

if curv_r and curv_r.crs() != slope_units.crs():
    print("WARNING: curvature CRS differs; CURV_MEAN will be skipped.")
    curv_r = None


# ================================================================
# CREATE CLEAN MASTER OUTPUT
# ================================================================

actual_output = choose_output_path(OUTPUT_GPKG)
master = create_clean_output(slope_units, actual_output, OUTPUT_LAYER_NAME)
n_units = master.featureCount()
if n_units == 0:
    raise RuntimeError("Master layer contains no slope units.")

print("Master output:")
print("  file :", actual_output)
print("  layer:", OUTPUT_LAYER_NAME)
print("  units:", n_units)


# ================================================================
# TERRAIN GRID + STATS
# ================================================================

slope_ds = open_gdal_raster(slope_r)
aspect_ds = open_gdal_raster(aspect_r)
dem_ds = open_gdal_raster(dem_r)

if not same_grid(slope_ds, aspect_ds):
    raise RuntimeError("slope_deg and aspect_deg are not aligned.")
if not same_grid(slope_ds, dem_ds):
    raise RuntimeError("slope_deg and dem_filled are not aligned.")

curv_ds = open_gdal_raster(curv_r) if curv_r else None
if curv_ds and not same_grid(slope_ds, curv_ds):
    print("WARNING: curvature_total is not aligned; CURV_MEAN will be skipped.")
    curv_ds = None

terrain_zone_path = os.path.join(TEMP_FOLDER, "slope_unit_ids_terrain.tif")
if os.path.exists(terrain_zone_path):
    try:
        os.remove(terrain_zone_path)
    except Exception:
        terrain_zone_path = os.path.join(
            TEMP_FOLDER,
            f"slope_unit_ids_terrain_{datetime.now().strftime('%H%M%S')}.tif",
        )

print("Rasterizing slope-unit IDs to terrain grid...")
terrain_zone_ds = create_zone_raster(
    slope_ds, actual_output, OUTPUT_LAYER_NAME, terrain_zone_path
)
zone = terrain_zone_ds.GetRasterBand(1).ReadAsArray()
represented = np.unique(zone[zone > 0]).size
print("Slope units represented by >=1 terrain pixel:", represented, "/", n_units)
if represented < n_units:
    print("WARNING:", n_units - represented, "units have no terrain pixel centre; raster stats will be NULL.")

slope_band = slope_ds.GetRasterBand(1)
aspect_band = aspect_ds.GetRasterBand(1)
dem_band = dem_ds.GetRasterBand(1)

slope_arr = slope_band.ReadAsArray().astype(np.float32)
aspect_arr = aspect_band.ReadAsArray().astype(np.float32)
dem_arr = dem_band.ReadAsArray().astype(np.float32)

base = zone > 0
slope_ok = base & valid_mask(slope_arr, slope_band)
aspect_ok = base & valid_mask(aspect_arr, aspect_band) & (aspect_arr >= 0) & (aspect_arr <= 360)
dem_ok = base & valid_mask(dem_arr, dem_band)

print("Calculating slope statistics...")
s_mean, slope_count = bincount_mean(zone, slope_arr, slope_ok, n_units)
_, s_max = grouped_min_max(zone, slope_arr, slope_ok, n_units)
if CALCULATE_SLOPE_PERCENTILES:
    print("Calculating exact P75/P90...")
    pct = grouped_percentiles(zone, slope_arr, slope_ok, n_units, (75, 90))
    s_p75 = pct[75]
    s_p90 = pct[90]
else:
    s_p75 = np.full(n_units + 1, np.nan)
    s_p90 = np.full(n_units + 1, np.nan)

print("Calculating circular aspect statistics...")
asp_mean, asp_conc, aspect_count = circular_aspect(zone, aspect_arr, aspect_ok, n_units)

print("Calculating elevation and relief...")
z_min, z_max = grouped_min_max(zone, dem_arr, dem_ok, n_units)
relief = z_max - z_min

curv_mean = np.full(n_units + 1, np.nan)
if curv_ds:
    print("Calculating mean curvature...")
    cb = curv_ds.GetRasterBand(1)
    ca = cb.ReadAsArray().astype(np.float32)
    curv_ok = base & valid_mask(ca, cb)
    curv_mean, _ = bincount_mean(zone, ca, curv_ok, n_units)


# ================================================================
# LSI + SUSCEPTIBILITY CLASSES
# ================================================================

print("Processing LSI raster...")
lsi_ds = open_gdal_raster(lsi_r)
lsi_zone_path = os.path.join(TEMP_FOLDER, "slope_unit_ids_lsi.tif")
if os.path.exists(lsi_zone_path):
    try:
        os.remove(lsi_zone_path)
    except Exception:
        lsi_zone_path = os.path.join(
            TEMP_FOLDER,
            f"slope_unit_ids_lsi_{datetime.now().strftime('%H%M%S')}.tif",
        )

lsi_zone_ds = create_zone_raster(lsi_ds, actual_output, OUTPUT_LAYER_NAME, lsi_zone_path)
lsi_zone = lsi_zone_ds.GetRasterBand(1).ReadAsArray()
lsi_band = lsi_ds.GetRasterBand(1)
lsi_arr = lsi_band.ReadAsArray().astype(np.float32)
lsi_ok = (lsi_zone > 0) & valid_mask(lsi_arr, lsi_band)
mean_lsi, lsi_count = bincount_mean(lsi_zone, lsi_arr, lsi_ok, n_units)

pct_classes = {i: np.full(n_units + 1, np.nan) for i in range(1, 6)}
high_pct = np.full(n_units + 1, np.nan)
dom_class = np.zeros(n_units + 1, dtype=np.int16)

if all(class_layers[i] is not None for i in range(1, 6)):
    print("Rasterizing susceptibility Class 1-5 polygons...")
    class_path = os.path.join(TEMP_FOLDER, "susceptibility_classes_1_5.tif")
    if os.path.exists(class_path):
        try:
            os.remove(class_path)
        except Exception:
            class_path = os.path.join(
                TEMP_FOLDER,
                f"susceptibility_classes_1_5_{datetime.now().strftime('%H%M%S')}.tif",
            )

    class_ds, held_sources = rasterize_classes(lsi_ds, class_layers, class_path)
    class_arr = class_ds.GetRasterBand(1).ReadAsArray()

    counts = {}
    for i in range(1, 6):
        mask = (lsi_zone > 0) & (class_arr == i)
        counts[i] = np.bincount(
            lsi_zone[mask].astype(np.int64), minlength=n_units + 1
        )
    total = sum(counts[i] for i in range(1, 6))
    good = total > 0
    for i in range(1, 6):
        pct_classes[i][good] = counts[i][good] / total[good] * 100.0
    high_pct[good] = pct_classes[4][good] + pct_classes[5][good]
    stack = np.vstack([np.nan_to_num(pct_classes[i], nan=-1.0) for i in range(1, 6)])
    best = np.argmax(stack, axis=0) + 1
    dom_class[good] = best[good]
    held_sources = None
    class_ds = None
else:
    print("WARNING: one or more Class_1..Class_5 layers are missing.")
    print("Class-percentage fields will remain NULL.")


# ================================================================
# WRITE ATTRIBUTES
# ================================================================

print("Writing statistics to master layer...")
field_names = [
    "S_MEAN", "S_P75", "S_P90", "S_MAX",
    "ASP_MEAN", "ASP_CONC",
    "Z_MIN", "Z_MAX", "RELIEF", "CURV_MEAN",
    "MEAN_LSI",
    "PCT_C1", "PCT_C2", "PCT_C3", "PCT_C4", "PCT_C5",
    "HIGH_PCT", "DOM_CLASS",
]
idx = {name: master.fields().indexOf(name) for name in field_names}
for name, i in idx.items():
    if i < 0:
        raise RuntimeError(f"Expected output field missing: {name}")

changes = {}
for feat in master.getFeatures():
    su = int(feat["SU_ID"])
    values = {
        idx["S_MEAN"]: safe_float(s_mean[su]),
        idx["S_P75"]: safe_float(s_p75[su]),
        idx["S_P90"]: safe_float(s_p90[su]),
        idx["S_MAX"]: safe_float(s_max[su]),
        idx["ASP_MEAN"]: safe_float(asp_mean[su]),
        idx["ASP_CONC"]: safe_float(asp_conc[su]),
        idx["Z_MIN"]: safe_float(z_min[su]),
        idx["Z_MAX"]: safe_float(z_max[su]),
        idx["RELIEF"]: safe_float(relief[su]),
        idx["CURV_MEAN"]: safe_float(curv_mean[su]),
        idx["MEAN_LSI"]: safe_float(mean_lsi[su]),
        idx["PCT_C1"]: safe_float(pct_classes[1][su]),
        idx["PCT_C2"]: safe_float(pct_classes[2][su]),
        idx["PCT_C3"]: safe_float(pct_classes[3][su]),
        idx["PCT_C4"]: safe_float(pct_classes[4][su]),
        idx["PCT_C5"]: safe_float(pct_classes[5][su]),
        idx["HIGH_PCT"]: safe_float(high_pct[su]),
        idx["DOM_CLASS"]: int(dom_class[su]) if dom_class[su] > 0 else None,
    }
    changes[feat.id()] = values
    if len(changes) >= 5000:
        if not master.dataProvider().changeAttributeValues(changes):
            raise RuntimeError("Failed writing a statistics batch.")
        changes = {}

if changes:
    if not master.dataProvider().changeAttributeValues(changes):
        raise RuntimeError("Failed writing final statistics batch.")

master.triggerRepaint()


# ================================================================
# REOPEN FINAL LAYER CLEANLY
# ================================================================

remove_loaded_output_layers(actual_output, OUTPUT_LAYER_NAME)
master = None
final_layer = QgsVectorLayer(
    f"{actual_output}|layername={OUTPUT_LAYER_NAME}",
    OUTPUT_LAYER_NAME,
    "ogr",
)
if not final_layer.isValid():
    raise RuntimeError("Final master layer could not be reopened.")
QgsProject.instance().addMapLayer(final_layer)


# ================================================================
# DIAGNOSTICS
# ================================================================

print("\n==============================================")
print("MASTER SLOPE-UNIT DATASET COMPLETE")
print("==============================================")
print("Features:", final_layer.featureCount())
print("Output  :", actual_output)
print("\nCoverage diagnostics:")
print("  units without slope pixels :", int(np.sum(slope_count[1:] == 0)))
print("  units without aspect pixels:", int(np.sum(aspect_count[1:] == 0)))
print("  units without LSI pixels   :", int(np.sum(lsi_count[1:] == 0)))
print("\nASP_CONC: 1.0 = very coherent aspect; 0.0 = highly variable aspect.")
print("Next step: use these attributes to define plausible release/source zones.")

terrain_zone_ds = None
lsi_zone_ds = None
slope_ds = None
aspect_ds = None
dem_ds = None
curv_ds = None
lsi_ds = None

print("Done.")



