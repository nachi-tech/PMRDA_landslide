# -*- coding: utf-8 -*-
"""
PMRDA slope-face generator for QGIS

Inputs expected in the current QGIS project:
    slope_deg
    aspect_deg
    05_FINAL_RETAINED_RIDGES

Outputs:
    F:/sim_repos/PMRDA_landslide/data/slope_faces.gpkg
    layer: slope_faces_final

Method:
    1. Mask terrain below MIN_SLOPE_DEG
    2. Classify aspect into 16 directions
    3. Polygonize contiguous steep/aspect regions
    4. Remove class 0
    5. Fix geometries
    6. Split polygons using retained ridge lines
    7. Convert multipart to singlepart
    8. Remove very small fragments
    9. Save final layer to GeoPackage
    10. Add SU_ID, AREA_HA and ASPECT_TXT
"""

from qgis.core import (
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsField,
    QgsProcessingFeedback
)
from qgis.PyQt.QtCore import QVariant
import processing
import os

SLOPE_LAYER_NAME = "slope_deg"
ASPECT_LAYER_NAME = "aspect_deg"
RIDGE_LAYER_NAME = "05_FINAL_RETAINED_RIDGES"

MIN_SLOPE_DEG = 15.0
MIN_AREA_M2 = 5000.0

OUTPUT_GPKG = r"F:/sim_repos/PMRDA_landslide/data/slope_faces_8dir.gpkg"
OUTPUT_LAYER_NAME = "slope_faces_final"

TEMP_FOLDER = r"F:/sim_repos/PMRDA_landslide/data/temp_slope_faces"
CLASSIFIED_RASTER = os.path.join(TEMP_FOLDER, "steep_aspect_8sector.tif")

os.makedirs(TEMP_FOLDER, exist_ok=True)
os.makedirs(os.path.dirname(OUTPUT_GPKG), exist_ok=True)

feedback = QgsProcessingFeedback()


def get_layer(name):
    layers = QgsProject.instance().mapLayersByName(name)
    if not layers:
        raise RuntimeError(f"Layer not found in current QGIS project: {name}")
    return layers[0]


def check_raster_alignment(a, b):
    tol = 1e-6

    if a.crs() != b.crs():
        raise RuntimeError(
            f"Raster CRS mismatch: {a.name()}={a.crs().authid()} "
            f"vs {b.name()}={b.crs().authid()}"
        )

    if abs(a.rasterUnitsPerPixelX() - b.rasterUnitsPerPixelX()) > tol:
        raise RuntimeError("Raster X pixel sizes differ.")

    if abs(a.rasterUnitsPerPixelY() - b.rasterUnitsPerPixelY()) > tol:
        raise RuntimeError("Raster Y pixel sizes differ.")

    ea = a.extent()
    eb = b.extent()

    extent_diff = max(
        abs(ea.xMinimum() - eb.xMinimum()),
        abs(ea.xMaximum() - eb.xMaximum()),
        abs(ea.yMinimum() - eb.yMinimum()),
        abs(ea.yMaximum() - eb.yMaximum()),
    )

    if extent_diff > 0.1:
        raise RuntimeError(
            "slope_deg and aspect_deg do not have the same extent. "
            "Align/resample them before running this script."
        )


slope = get_layer(SLOPE_LAYER_NAME)
aspect = get_layer(ASPECT_LAYER_NAME)
ridges = get_layer(RIDGE_LAYER_NAME)

print("Inputs found:")
print("  slope :", slope.name())
print("  aspect:", aspect.name())
print("  ridges:", ridges.name())

print("\nCRS:")
print("  slope :", slope.crs().authid())
print("  aspect:", aspect.crs().authid())
print("  ridges:", ridges.crs().authid())

if slope.crs() != ridges.crs():
    raise RuntimeError("slope_deg and retained ridge lines have different CRS.")

check_raster_alignment(slope, aspect)

print("\nRaster geometry check:")
print(
    "  slope :",
    slope.width(), "x", slope.height(),
    "pixel =", slope.rasterUnitsPerPixelX(),
    slope.rasterUnitsPerPixelY()
)
print(
    "  aspect:",
    aspect.width(), "x", aspect.height(),
    "pixel =", aspect.rasterUnitsPerPixelX(),
    aspect.rasterUnitsPerPixelY()
)
print("  slope extent :", slope.extent().toString())
print("  aspect extent:", aspect.extent().toString())


# STEP 1
print("\nSTEP 1 - Creating steep/aspect raster...")

ext = slope.extent()
extent_string = (
    f"{ext.xMinimum()},"
    f"{ext.xMaximum()},"
    f"{ext.yMinimum()},"
    f"{ext.yMaximum()} "
    f"[{slope.crs().authid()}]"
)

print("Raster extent:")
print(extent_string)

expression = f'''
(
    ("{SLOPE_LAYER_NAME}@1" >= {MIN_SLOPE_DEG})
    *
    (
        ((("{ASPECT_LAYER_NAME}@1" >= 337.5) OR
          ("{ASPECT_LAYER_NAME}@1" < 22.5)) * 1)

        +

        ((("{ASPECT_LAYER_NAME}@1" >= 22.5) AND
          ("{ASPECT_LAYER_NAME}@1" < 67.5)) * 2)

        +

        ((("{ASPECT_LAYER_NAME}@1" >= 67.5) AND
          ("{ASPECT_LAYER_NAME}@1" < 112.5)) * 3)

        +

        ((("{ASPECT_LAYER_NAME}@1" >= 112.5) AND
          ("{ASPECT_LAYER_NAME}@1" < 157.5)) * 4)

        +

        ((("{ASPECT_LAYER_NAME}@1" >= 157.5) AND
          ("{ASPECT_LAYER_NAME}@1" < 202.5)) * 5)

        +

        ((("{ASPECT_LAYER_NAME}@1" >= 202.5) AND
          ("{ASPECT_LAYER_NAME}@1" < 247.5)) * 6)

        +

        ((("{ASPECT_LAYER_NAME}@1" >= 247.5) AND
          ("{ASPECT_LAYER_NAME}@1" < 292.5)) * 7)

        +

        ((("{ASPECT_LAYER_NAME}@1" >= 292.5) AND
          ("{ASPECT_LAYER_NAME}@1" < 337.5)) * 8)
    )
)
'''


result = processing.run(
    "qgis:rastercalculator",
    {
        "EXPRESSION": expression,
        "LAYERS": [slope, aspect],
        "CELLSIZE": abs(slope.rasterUnitsPerPixelX()),
        "EXTENT": extent_string,
        "CRS": slope.crs(),
        "OUTPUT": CLASSIFIED_RASTER
    },
    feedback=feedback
)

classified_path = result["OUTPUT"]
print("Raster calculator finished.")
print("Output:", classified_path)

classified = QgsRasterLayer(classified_path, "steep_aspect_8sector")
if not classified.isValid():
    raise RuntimeError("The 16-sector classified raster could not be loaded.")

for lyr in QgsProject.instance().mapLayersByName("steep_aspect_8sector"):
    QgsProject.instance().removeMapLayer(lyr.id())

QgsProject.instance().addMapLayer(classified)


# STEP 2
print("\nSTEP 2 - Polygonizing...")

result_poly = processing.run(
    "gdal:polygonize",
    {
        "INPUT": classified_path,
        "BAND": 1,
        "FIELD": "ASP_SECTOR",
        "EIGHT_CONNECTEDNESS": True,
        "EXTRA": "",
        "OUTPUT": "TEMPORARY_OUTPUT"
    },
    feedback=feedback
)

poly_raw = result_poly["OUTPUT"]
print("Polygonization complete.")
print("Step 2 output:", poly_raw)


# STEP 3
print("\nSTEP 3 - Removing terrain below slope threshold...")

result_steep = processing.run(
    "native:extractbyexpression",
    {
        "INPUT": poly_raw,
        "EXPRESSION": '"ASP_SECTOR" > 0',
        "OUTPUT": "TEMPORARY_OUTPUT"
    },
    feedback=feedback
)

poly_steep = result_steep["OUTPUT"]
print("Steep-terrain polygons extracted.")


# STEP 4
print("\nSTEP 4 - Fixing polygon geometries...")

result_fixed = processing.run(
    "native:fixgeometries",
    {
        "INPUT": poly_steep,
        "METHOD": 1,
        "OUTPUT": "TEMPORARY_OUTPUT"
    },
    feedback=feedback
)

poly_fixed = result_fixed["OUTPUT"]
print("Geometry repair complete.")


# STEP 5
print("\nSTEP 5 - Splitting slope faces with ridge lines...")

result_split = processing.run(
    "native:splitwithlines",
    {
        "INPUT": poly_fixed,
        "LINES": ridges,
        "OUTPUT": "TEMPORARY_OUTPUT"
    },
    feedback=feedback
)

poly_split = result_split["OUTPUT"]
print("Ridge splitting complete.")


# STEP 6
print("\nSTEP 6 - Converting multipart polygons to singlepart...")

result_single = processing.run(
    "native:multiparttosingleparts",
    {
        "INPUT": poly_split,
        "OUTPUT": "TEMPORARY_OUTPUT"
    },
    feedback=feedback
)

poly_single = result_single["OUTPUT"]
print("Singlepart conversion complete.")


# STEP 7
print(
    f"\nSTEP 7 - Removing polygons smaller than "
    f"{MIN_AREA_M2:.0f} m2..."
)

result_final_temp = processing.run(
    "native:extractbyexpression",
    {
        "INPUT": poly_single,
        "EXPRESSION": f"$area >= {MIN_AREA_M2}",
        "OUTPUT": "TEMPORARY_OUTPUT"
    },
    feedback=feedback
)

poly_final_temp = result_final_temp["OUTPUT"]
print("Small-fragment filtering complete.")



# ================================================================
# STEP 7B - REMOVE INHERITED FID FIELD
# ================================================================

print("\nSTEP 7B - Removing inherited feature-ID fields...")

# Load the temporary result as a vector layer if necessary
if isinstance(poly_final_temp, QgsVectorLayer):
    temp_check = poly_final_temp
else:
    temp_check = QgsVectorLayer(
        str(poly_final_temp),
        "slope_faces_temp_check",
        "ogr"
    )

if not temp_check.isValid():
    raise RuntimeError(
        "Could not load temporary slope-face layer."
    )

field_names = [f.name() for f in temp_check.fields()]

print("Fields before cleanup:")
print(field_names)

# Remove any field which could conflict with the GeoPackage
# primary feature-ID column.
fields_to_remove = [
    name
    for name in field_names
    if name.lower() in ("fid", "ogc_fid")
]

if fields_to_remove:

    print("Removing:", fields_to_remove)

    result_no_fid = processing.run(
        "native:deletecolumn",
        {
            "INPUT": poly_final_temp,
            "COLUMN": fields_to_remove,
            "OUTPUT": "TEMPORARY_OUTPUT"
        },
        feedback=feedback
    )

    poly_final_clean = result_no_fid["OUTPUT"]

else:

    print("No inherited FID attribute found.")
    poly_final_clean = poly_final_temp


# ================================================================
# STEP 8 - SAVE AS A COMPLETELY NEW GEOPACKAGE
# ================================================================

print("\nSTEP 8 - Saving final GeoPackage layer...")

from qgis.core import QgsVectorFileWriter
import os


# ------------------------------------------------
# Resolve cleaned temporary layer
# ------------------------------------------------

if isinstance(poly_final_clean, QgsVectorLayer):
    temp_layer = poly_final_clean
else:
    temp_layer = QgsVectorLayer(
        str(poly_final_clean),
        "slope_faces_clean",
        "ogr"
    )

if not temp_layer.isValid():
    raise RuntimeError(
        "Cleaned temporary polygon layer could not be loaded."
    )

print("Temporary layer valid.")
print("Features to save:", temp_layer.featureCount())

print("Fields being written:")
print([f.name() for f in temp_layer.fields()])


# ------------------------------------------------
# Remove existing output layer from QGIS project
# ------------------------------------------------

for lyr in list(
    QgsProject.instance().mapLayersByName(OUTPUT_LAYER_NAME)
):
    QgsProject.instance().removeMapLayer(lyr.id())


# ------------------------------------------------
# IMPORTANT:
# Delete previous failed output file entirely
# ------------------------------------------------

if os.path.exists(OUTPUT_GPKG):

    print("Deleting existing output GeoPackage:")
    print(OUTPUT_GPKG)

    try:
        os.remove(OUTPUT_GPKG)
    except Exception as e:
        raise RuntimeError(
            "The previous GeoPackage could not be deleted.\n"
            "It may still be locked by QGIS.\n\n"
            f"{OUTPUT_GPKG}\n\n"
            f"Error: {e}"
        )


# Remove possible SQLite WAL/SHM leftovers as well

for suffix in ["-wal", "-shm"]:

    extra_file = OUTPUT_GPKG + suffix

    if os.path.exists(extra_file):
        try:
            os.remove(extra_file)
        except Exception:
            pass


# ------------------------------------------------
# Writer settings
# ------------------------------------------------

options = QgsVectorFileWriter.SaveVectorOptions()

options.driverName = "GPKG"
options.layerName = OUTPUT_LAYER_NAME

options.actionOnExistingFile = (
    QgsVectorFileWriter.CreateOrOverwriteFile
)


# ------------------------------------------------
# Write
# ------------------------------------------------

result = QgsVectorFileWriter.writeAsVectorFormatV3(
    temp_layer,
    OUTPUT_GPKG,
    QgsProject.instance().transformContext(),
    options
)

error_code = result[0]
error_message = result[1] if len(result) > 1 else ""

if error_code != QgsVectorFileWriter.NoError:
    raise RuntimeError(
        "\nCould not create GeoPackage.\n"
        f"Error code: {error_code}\n"
        f"Message: {error_message}\n"
        f"Target: {OUTPUT_GPKG}"
    )

print("GeoPackage successfully created.")
print("File :", OUTPUT_GPKG)
print("Layer:", OUTPUT_LAYER_NAME)


# ================================================================
# STEP 9 - LOAD CLEAN FINAL LAYER
# ================================================================

print("\nSTEP 9 - Loading final layer...")

final_uri = (
    f"{OUTPUT_GPKG}|layername={OUTPUT_LAYER_NAME}"
)

final_layer = QgsVectorLayer(
    final_uri,
    OUTPUT_LAYER_NAME,
    "ogr"
)

if not final_layer.isValid():
    raise RuntimeError(
        "GeoPackage was written but final layer could not be opened."
    )

QgsProject.instance().addMapLayer(final_layer)

print("Final layer successfully loaded.")
print("Final feature count:", final_layer.featureCount())

print("Final fields:")
print([f.name() for f in final_layer.fields()])

# ================================================================
# STEP 10 - ADD OUTPUT ATTRIBUTES
# ================================================================

print("\nSTEP 10 - Adding output attributes...")

provider = final_layer.dataProvider()

field_names = [
    field.name()
    for field in final_layer.fields()
]

new_fields = []

if "SU_ID" not in field_names:
    new_fields.append(
        QgsField(
            "SU_ID",
            QVariant.Int
        )
    )

if "AREA_HA" not in field_names:
    new_fields.append(
        QgsField(
            "AREA_HA",
            QVariant.Double,
            len=20,
            prec=4
        )
    )

if "ASPECT_TXT" not in field_names:
    new_fields.append(
        QgsField(
            "ASPECT_TXT",
            QVariant.String,
            len=3
        )
    )

if new_fields:
    if not provider.addAttributes(new_fields):
        raise RuntimeError(
            "Could not add fields to final GeoPackage layer."
        )

    final_layer.updateFields()

print("Output attributes ready.")



# STEP 10
provider = final_layer.dataProvider()
field_names = [f.name() for f in final_layer.fields()]
new_fields = []

if "SU_ID" not in field_names:
    new_fields.append(QgsField("SU_ID", QVariant.Int))

if "AREA_HA" not in field_names:
    new_fields.append(
        QgsField("AREA_HA", QVariant.Double, len=20, prec=4)
    )

if "ASPECT_TXT" not in field_names:
    new_fields.append(
        QgsField("ASPECT_TXT", QVariant.String, len=3)
    )

if new_fields:
    provider.addAttributes(new_fields)
    final_layer.updateFields()


# STEP 11
sector_names = {
    1: "N",
    2: "NE",
    3: "E",
    4: "SE",
    5: "S",
    6: "SW",
    7: "W",
    8: "NW"
}


idx_id = final_layer.fields().indexOf("SU_ID")
idx_area = final_layer.fields().indexOf("AREA_HA")
idx_sector = final_layer.fields().indexOf("ASP_SECTOR")
idx_text = final_layer.fields().indexOf("ASPECT_TXT")

if idx_sector < 0:
    raise RuntimeError("ASP_SECTOR field is missing from final polygon layer.")

final_layer.startEditing()
counter = 1

for feature in final_layer.getFeatures():
    geom = feature.geometry()

    if geom is None or geom.isEmpty():
        continue

    area_ha = geom.area() / 10000.0

    try:
        sector = int(feature["ASP_SECTOR"])
    except Exception:
        sector = 0

    feature[idx_id] = counter
    feature[idx_area] = area_ha
    feature[idx_text] = sector_names.get(sector, "?")
    final_layer.updateFeature(feature)
    counter += 1

if not final_layer.commitChanges():
    raise RuntimeError(
        "Could not commit attribute changes to final layer."
    )


print("\n==============================================")
print("SLOPE FACE GENERATION COMPLETE")
print("==============================================")
print("Minimum slope :", MIN_SLOPE_DEG, "degrees")
print("Minimum area  :", MIN_AREA_M2, "m2")
print("Aspect sectors: 8")
print("Final units   :", final_layer.featureCount())

print("\nAspect codes:")
print("1 = N")
print("2 = NE")
print("3 = E")
print("4 = SE")
print("5 = S")
print("6 = SW")
print("7 = W")
print("8 = NW")


print("\nFinal layer:")
print(OUTPUT_LAYER_NAME)

print("\nGeoPackage:")
print(OUTPUT_GPKG)

print(
    "\nNext recommended step: calculate mean/P75/P90 slope, "
    "circular mean aspect, aspect concentration and relief for "
    "each candidate slope face, then merge neighbouring fragments "
    "that form one coherent terrain face."
)




