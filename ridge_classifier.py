# -*- coding: utf-8 -*-
"""
PMRDA ridge extractor / dominant crest selector
Designed for QGIS Python Editor / Python Console.

Purpose
-------
1. Clean duplicate/overlapping basin-boundary linework.
2. Split the linework into regular analysis segments while preserving true geometry.
3. Sample DEM, slope and aspect on both sides of each segment at multiple distances.
4. Score how strongly each segment behaves as a ridge.
5. Remove weak / valley / side-slope segments.
6. Suppress nearby parallel weaker ridge candidates so only the dominant crest remains.
7. Merge retained segments into longer final ridge lines.

Inputs already loaded in QGIS
-----------------------------
- Basin boundary lines
- DEM
- Slope raster in degrees
- Aspect raster in degrees

All layers should use the same projected CRS, ideally EPSG:32643.
"""

from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsSpatialIndex,
    QgsRectangle
)
from qgis.PyQt.QtCore import QVariant
import math


# ============================================================
# USER SETTINGS
# ============================================================

BASIN_LAYER_NAME = "basin_lines"
DEM_LAYER_NAME = "dem_working"
SLOPE_LAYER_NAME = "slope_deg"
ASPECT_LAYER_NAME = "aspect_deg"

# Analysis geometry
SEGMENT_LENGTH = 100.0          # metres
TANGENT_DISTANCE = 20.0         # +/- metres around midpoint for local line direction

# Multi-distance cross-slope sampling.
# With a ~30 m DEM these correspond to ~2, 3 and 4 pixels away.
SAMPLE_DISTANCES = [60.0, 90.0, 120.0]

# Initial geomorphic filtering
MIN_RIDGE_STRENGTH = 8.0        # metres; minimum persistent drop on BOTH sides
MIN_ASPECT_DIFF = 80.0          # degrees; helps reject side slopes
MIN_SIDE_SLOPE = 8.0            # degrees; minimum mean slope on both sides

# Nearby-parallel-line suppression
SUPPRESSION_DISTANCE = 75.0     # metres
MAX_PARALLEL_ANGLE_DIFF = 35.0  # degrees

# Small gaps between selected segments can be bridged at this distance.
# Keep conservative relative to 30 m DEM.
CONNECT_DISTANCE = 40.0         # metres


# ============================================================
# BASIC HELPERS
# ============================================================

project = QgsProject.instance()


def get_layer(name):
    layers = project.mapLayersByName(name)
    if not layers:
        raise Exception(f"Layer not found: {name}")
    return layers[0]


basin_layer = get_layer(BASIN_LAYER_NAME)
dem_layer = get_layer(DEM_LAYER_NAME)
slope_layer = get_layer(SLOPE_LAYER_NAME)
aspect_layer = get_layer(ASPECT_LAYER_NAME)


def sample_raster(layer, point):
    """Sample raster band 1 at a point."""
    value, ok = layer.dataProvider().sample(QgsPointXY(point), 1)
    if not ok or value is None:
        return None
    try:
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except Exception:
        return None


def angular_difference(a1, a2):
    """Smallest circular angular difference, 0..180 degrees."""
    if a1 is None or a2 is None:
        return None
    diff = abs(a1 - a2) % 360.0
    if diff > 180.0:
        diff = 360.0 - diff
    return diff


def line_orientation_deg(p1, p2):
    """
    Undirected line orientation in degrees, 0..180.
    0 = east-west, 90 = north-south.
    """
    dx = p2.x() - p1.x()
    dy = p2.y() - p1.y()
    a = math.degrees(math.atan2(dy, dx)) % 180.0
    return a


def orientation_difference(a1, a2):
    """Difference between two undirected line orientations, 0..90."""
    d = abs(a1 - a2) % 180.0
    return min(d, 180.0 - d)


def point_at_distance(geom, d):
    g = geom.interpolate(d)
    if g is None or g.isEmpty():
        return None
    return g.asPoint()


def true_substring(line_geom, start_d, end_d):
    """Preserve the actual curved geometry between two distances."""
    curve = line_geom.constGet()
    if not hasattr(curve, "curveSubstring"):
        return None
    sub = curve.curveSubstring(start_d, end_d)
    if sub is None:
        return None
    return QgsGeometry(sub.clone())


# ============================================================
# CRS CHECK
# ============================================================

crss = {
    basin_layer.crs().authid(),
    dem_layer.crs().authid(),
    slope_layer.crs().authid(),
    aspect_layer.crs().authid()
}

if len(crss) != 1:
    raise Exception(
        "Input CRS mismatch. Reproject all inputs to the same projected CRS first.\n"
        f"Found: {crss}"
    )

print("Input CRS:", basin_layer.crs().authid())


# ============================================================
# STEP 1: CLEAN THE BASIN BOUNDARY NETWORK
#
# Unary union removes exact duplicated overlaps and nodes
# intersections. lineMerge then joins contiguous linework where
# topology permits.
# ============================================================

print("Step 1/6: cleaning basin boundary linework...")

input_geoms = []
for f in basin_layer.getFeatures():
    g = f.geometry()
    if g is not None and not g.isEmpty():
        input_geoms.append(g)

if not input_geoms:
    raise Exception("No valid geometries in basin layer.")

clean_union = QgsGeometry.unaryUnion(input_geoms)

# Try to merge connected lines after union/noding.
try:
    clean_union = clean_union.mergeLines()
except Exception:
    pass

clean_layer = QgsVectorLayer(
    f"MultiLineString?crs={basin_layer.crs().authid()}",
    "01_basin_lines_clean",
    "memory"
)
clean_pr = clean_layer.dataProvider()
clean_pr.addAttributes([QgsField("ID", QVariant.Int)])
clean_layer.updateFields()

cf = QgsFeature(clean_layer.fields())
cf["ID"] = 1
cf.setGeometry(clean_union)
clean_pr.addFeature(cf)
clean_layer.updateExtents()
project.addMapLayer(clean_layer)


# ============================================================
# STEP 2: REGULAR SEGMENTATION + MULTI-DISTANCE SAMPLING
# ============================================================

print("Step 2/6: segmenting and sampling terrain...")

analysis = QgsVectorLayer(
    f"LineString?crs={basin_layer.crs().authid()}",
    "02_ridge_segments_scored",
    "memory"
)
ap = analysis.dataProvider()

fields = [
    QgsField("SEG_ID", QVariant.Int),
    QgsField("PARENT_ID", QVariant.Int),
    QgsField("ORIENT", QVariant.Double),

    QgsField("Z_C", QVariant.Double),

    QgsField("DROP_L60", QVariant.Double),
    QgsField("DROP_R60", QVariant.Double),
    QgsField("DROP_L90", QVariant.Double),
    QgsField("DROP_R90", QVariant.Double),
    QgsField("DROP_L120", QVariant.Double),
    QgsField("DROP_R120", QVariant.Double),

    QgsField("RIDGE_STR", QVariant.Double),
    QgsField("RIDGE_AVG", QVariant.Double),

    QgsField("S_L", QVariant.Double),
    QgsField("S_R", QVariant.Double),

    QgsField("A_L", QVariant.Double),
    QgsField("A_R", QVariant.Double),
    QgsField("ASP_DIFF", QVariant.Double),

    QgsField("KEEP0", QVariant.Int),
    QgsField("TYPE", QVariant.String)
]
ap.addAttributes(fields)
analysis.updateFields()


# Break clean union into individual line parts.
parts = []
if clean_union.isMultipart():
    for g in clean_union.asGeometryCollection():
        if g is not None and not g.isEmpty():
            parts.append(g)
else:
    parts = [clean_union]

seg_id = 1
parent_id = 1

for part in parts:

    total_length = part.length()
    if total_length < SEGMENT_LENGTH * 0.5:
        continue

    start_d = 0.0

    while start_d < total_length:

        end_d = min(start_d + SEGMENT_LENGTH, total_length)
        if (end_d - start_d) < SEGMENT_LENGTH * 0.5:
            break

        mid_d = (start_d + end_d) / 2.0

        seg_geom = true_substring(part, start_d, end_d)
        if seg_geom is None or seg_geom.isEmpty():
            start_d = end_d
            continue

        center = point_at_distance(part, mid_d)
        if center is None:
            start_d = end_d
            continue

        before = point_at_distance(part, max(0.0, mid_d - TANGENT_DISTANCE))
        after = point_at_distance(part, min(total_length, mid_d + TANGENT_DISTANCE))
        if before is None or after is None:
            start_d = end_d
            continue

        dx = after.x() - before.x()
        dy = after.y() - before.y()
        tang_len = math.hypot(dx, dy)
        if tang_len == 0:
            start_d = end_d
            continue

        orient = line_orientation_deg(before, after)

        ux = dx / tang_len
        uy = dy / tang_len
        px = -uy
        py = ux

        zc = sample_raster(dem_layer, center)
        if zc is None:
            start_d = end_d
            continue

        drops_l = []
        drops_r = []

        valid = True
        for dist in SAMPLE_DISTANCES:
            lp = QgsPointXY(center.x() + px * dist, center.y() + py * dist)
            rp = QgsPointXY(center.x() - px * dist, center.y() - py * dist)

            zl = sample_raster(dem_layer, lp)
            zr = sample_raster(dem_layer, rp)

            if zl is None or zr is None:
                valid = False
                break

            drops_l.append(zc - zl)
            drops_r.append(zc - zr)

        if not valid:
            start_d = end_d
            continue

        # Persistent ridge strength:
        # For each distance, the weaker of the two sides controls the ridge.
        # Then take the median-ish central scale behavior using the minimum
        # across distances, making this conservative against pixel noise.
        pair_strengths = [
            min(drops_l[i], drops_r[i]) for i in range(len(SAMPLE_DISTANCES))
        ]

        # Conservative persistent ridge criterion
        ridge_strength = min(pair_strengths)

        # Average bilateral relief across all sample distances
        ridge_avg = sum(
            (drops_l[i] + drops_r[i]) / 2.0
            for i in range(len(SAMPLE_DISTANCES))
        ) / len(SAMPLE_DISTANCES)

        # Use 90 m samples for slope/aspect diagnostics
        diag_dist = 90.0
        lp90 = QgsPointXY(center.x() + px * diag_dist, center.y() + py * diag_dist)
        rp90 = QgsPointXY(center.x() - px * diag_dist, center.y() - py * diag_dist)

        sl = sample_raster(slope_layer, lp90)
        sr = sample_raster(slope_layer, rp90)
        al = sample_raster(aspect_layer, lp90)
        ar = sample_raster(aspect_layer, rp90)
        adiff = angular_difference(al, ar)

        if sl is None or sr is None or adiff is None:
            start_d = end_d
            continue

        # Initial classification
        both_sides_fall = all(
            drops_l[i] > 0 and drops_r[i] > 0
            for i in range(len(SAMPLE_DISTANCES))
        )

        both_sides_rise = all(
            drops_l[i] < 0 and drops_r[i] < 0
            for i in range(len(SAMPLE_DISTANCES))
        )

        keep0 = 0
        rtype = "SIDE_OR_AMBIG"

        if both_sides_rise:
            rtype = "VALLEY"

        elif both_sides_fall:
            if (
                ridge_strength >= MIN_RIDGE_STRENGTH
                and adiff >= MIN_ASPECT_DIFF
                and sl >= MIN_SIDE_SLOPE
                and sr >= MIN_SIDE_SLOPE
            ):
                keep0 = 1
                rtype = "RIDGE_CANDIDATE"
            else:
                rtype = "WEAK_RIDGE"

        out = QgsFeature(analysis.fields())
        out.setGeometry(seg_geom)

        vals = {
            "SEG_ID": seg_id,
            "PARENT_ID": parent_id,
            "ORIENT": orient,
            "Z_C": zc,
            "DROP_L60": drops_l[0],
            "DROP_R60": drops_r[0],
            "DROP_L90": drops_l[1],
            "DROP_R90": drops_r[1],
            "DROP_L120": drops_l[2],
            "DROP_R120": drops_r[2],
            "RIDGE_STR": ridge_strength,
            "RIDGE_AVG": ridge_avg,
            "S_L": sl,
            "S_R": sr,
            "A_L": al,
            "A_R": ar,
            "ASP_DIFF": adiff,
            "KEEP0": keep0,
            "TYPE": rtype
        }

        for k, v in vals.items():
            out[k] = v

        ap.addFeature(out)

        seg_id += 1
        start_d = end_d

    parent_id += 1

analysis.updateExtents()
project.addMapLayer(analysis)

print("Segments scored:", seg_id - 1)


# ============================================================
# STEP 3: COLLECT INITIAL RIDGE CANDIDATES
# ============================================================

print("Step 3/6: selecting ridge candidates...")

candidate_feats = [
    f for f in analysis.getFeatures()
    if int(f["KEEP0"]) == 1
]

print("Initial ridge candidates:", len(candidate_feats))


# ============================================================
# STEP 4: NON-MAXIMUM SUPPRESSION OF NEARBY PARALLEL RIDGES
#
# Sort by ridge strength. Keep strongest first.
# If a weaker segment lies within SUPPRESSION_DISTANCE of an
# already-kept segment AND is approximately parallel, discard it.
#
# This is the step intended to turn clusters of nearby ridge
# candidates into one dominant crest.
# ============================================================

print("Step 4/6: suppressing nearby parallel weaker ridge lines...")

candidate_feats.sort(
    key=lambda f: (float(f["RIDGE_STR"]), float(f["RIDGE_AVG"])),
    reverse=True
)

kept = []
kept_layer_tmp = QgsVectorLayer(
    f"LineString?crs={basin_layer.crs().authid()}",
    "_kept_index_temp",
    "memory"
)
ktp = kept_layer_tmp.dataProvider()
ktp.addAttributes([
    QgsField("KID", QVariant.Int),
    QgsField("ORIENT", QVariant.Double)
])
kept_layer_tmp.updateFields()

sp_index = QgsSpatialIndex()
kept_by_id = {}

next_kid = 1

for f in candidate_feats:

    g = f.geometry()
    if g is None or g.isEmpty():
        continue

    orient = float(f["ORIENT"])

    bbox = g.boundingBox()
    search_box = QgsRectangle(
        bbox.xMinimum() - SUPPRESSION_DISTANCE,
        bbox.yMinimum() - SUPPRESSION_DISTANCE,
        bbox.xMaximum() + SUPPRESSION_DISTANCE,
        bbox.yMaximum() + SUPPRESSION_DISTANCE
    )

    suppress = False

    for kid in sp_index.intersects(search_box):
        kf = kept_by_id[kid]
        kg = kf.geometry()

        if g.distance(kg) > SUPPRESSION_DISTANCE:
            continue

        kd = orientation_difference(
            orient,
            float(kf["ORIENT"])
        )

        if kd <= MAX_PARALLEL_ANGLE_DIFF:
            suppress = True
            break

    if suppress:
        continue

    nf = QgsFeature(kept_layer_tmp.fields())
    nf.setGeometry(g)
    nf["KID"] = next_kid
    nf["ORIENT"] = orient
    ktp.addFeature(nf)

    # Spatial index requires feature id after provider insertion.
    # Retrieve last inserted feature from layer by building a copy with
    # a stable logical KID.
    added = list(kept_layer_tmp.getFeatures(
        f'"KID" = {next_kid}'
    ))
    if added:
        af = added[0]
        sp_index.addFeature(af)
        kept_by_id[af.id()] = af
        kept.append((f, af))

    next_kid += 1


# ============================================================
# STEP 5: CREATE RETAINED SEGMENT LAYER
# ============================================================

print("Step 5/6: creating retained ridge segment layer...")

selected = QgsVectorLayer(
    f"LineString?crs={basin_layer.crs().authid()}",
    "03_dominant_ridge_segments",
    "memory"
)
sp = selected.dataProvider()
sp.addAttributes(analysis.fields())
selected.updateFields()

for original_f, _ in kept:
    nf = QgsFeature(selected.fields())
    nf.setGeometry(original_f.geometry())
    for fld in selected.fields():
        name = fld.name()
        if name in original_f.fields().names():
            nf[name] = original_f[name]
    sp.addFeature(nf)

selected.updateExtents()
project.addMapLayer(selected)

print("Dominant ridge segments:", selected.featureCount())


# ============================================================
# STEP 6: FIND PARENT RIDGES WITH AT LEAST ONE DOMINANT PIECE
# ============================================================

print("Step 6/7: finding accepted parent ridges...")

dominant_parent_ids = set()

for f in selected.getFeatures():
    try:
        dominant_parent_ids.add(int(f["PARENT_ID"]))
    except Exception:
        pass

print("Accepted parent ridges:", len(dominant_parent_ids))


# ============================================================
# STEP 7: KEEP ALL SCORED SEGMENTS FROM ACCEPTED PARENTS
# ============================================================

print("Step 7/7: retaining all parts of accepted ridges...")

retained = QgsVectorLayer(
    f"LineString?crs={basin_layer.crs().authid()}",
    "04_RIDGES_WITH_DOMINANT_PART",
    "memory"
)

rp = retained.dataProvider()
rp.addAttributes(analysis.fields())
retained.updateFields()

for f in analysis.getFeatures():
    try:
        pid = int(f["PARENT_ID"])
    except Exception:
        continue

    if pid not in dominant_parent_ids:
        continue

    nf = QgsFeature(retained.fields())
    nf.setGeometry(f.geometry())

    for fld in retained.fields():
        name = fld.name()
        if name in f.fields().names():
            nf[name] = f[name]

    rp.addFeature(nf)

retained.updateExtents()
project.addMapLayer(retained)


# ============================================================
# MERGE EACH ACCEPTED PARENT INTO ONE FINAL FEATURE
# ============================================================

merged = QgsVectorLayer(
    f"MultiLineString?crs={basin_layer.crs().authid()}",
    "05_FINAL_RETAINED_RIDGES",
    "memory"
)

mp = merged.dataProvider()
mp.addAttributes([
    QgsField("PARENT_ID", QVariant.Int)
])
merged.updateFields()

for pid in sorted(dominant_parent_ids):

    geoms = [
        f.geometry()
        for f in retained.getFeatures(f'"PARENT_ID" = {pid}')
        if f.geometry() is not None and not f.geometry().isEmpty()
    ]

    if not geoms:
        continue

    g = QgsGeometry.unaryUnion(geoms)

    try:
        g = g.mergeLines()
    except Exception:
        pass

    out = QgsFeature(merged.fields())
    out["PARENT_ID"] = pid
    out.setGeometry(g)
    mp.addFeature(out)

merged.updateExtents()
project.addMapLayer(merged)

print("--------------------------------------------------")
print("Finished.")
print("02_ridge_segments_scored      = all scored pieces")
print("03_dominant_ridge_segments    = dominant pieces")
print("04_RIDGES_WITH_DOMINANT_PART  = ALL pieces from any parent ridge")
print("                                that contains >=1 dominant piece")
print("05_FINAL_RETAINED_RIDGES      = those accepted parents merged")
print("--------------------------------------------------")



