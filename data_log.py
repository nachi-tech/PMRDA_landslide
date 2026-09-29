from qgis.core import (
    QgsProject,
    QgsMapLayerType,
    QgsLayerTreeLayer,
    QgsRasterLayer,
    QgsVectorLayer
)
import csv
import json
import os
from datetime import datetime


# ============================================================
# OUTPUT LOCATION
# Change this if you want another folder.
# ============================================================

OUTPUT_FOLDER = os.path.expanduser("~/Desktop")

CSV_PATH = os.path.join(
    OUTPUT_FOLDER,
    "qgis_layer_inventory.csv"
)

JSON_PATH = os.path.join(
    OUTPUT_FOLDER,
    "qgis_layer_inventory.json"
)


# ============================================================
# HELPERS
# ============================================================

project = QgsProject.instance()


def get_group_path(layer_id):
    root = project.layerTreeRoot()
    node = root.findLayer(layer_id)

    if node is None:
        return ""

    names = []
    parent = node.parent()

    while parent is not None and parent != root:
        names.append(parent.name())
        parent = parent.parent()

    return " / ".join(reversed(names))


def extent_dict(ext):
    return {
        "xmin": ext.xMinimum(),
        "ymin": ext.yMinimum(),
        "xmax": ext.xMaximum(),
        "ymax": ext.yMaximum()
    }


def safe_raster_stats(layer, band):
    try:
        provider = layer.dataProvider()
        stats = provider.bandStatistics(band)

        return {
            "minimum": stats.minimumValue,
            "maximum": stats.maximumValue,
            "mean": stats.mean,
            "stddev": stats.stdDev
        }

    except Exception:
        return None


def safe_nodata(layer, band):
    try:
        provider = layer.dataProvider()

        if provider.sourceHasNoDataValue(band):
            return provider.sourceNoDataValue(band)

    except Exception:
        pass

    return None


# ============================================================
# COLLECT METADATA
# ============================================================

inventory = []


for layer in project.mapLayers().values():

    record = {
        "layer_id": layer.id(),
        "name": layer.name(),
        "group_path": get_group_path(layer.id()),
        "valid": layer.isValid(),
        "provider": layer.providerType(),
        "source": layer.source(),
        "crs_authid": layer.crs().authid(),
        "crs_description": layer.crs().description(),
        "extent": extent_dict(layer.extent()),
        "layer_type": None
    }


    # ========================================================
    # VECTOR
    # ========================================================

    if layer.type() == QgsMapLayerType.VectorLayer:

        record["layer_type"] = "vector"

        try:
            record["geometry_type"] = layer.geometryType()
        except Exception:
            record["geometry_type"] = None

        try:
            record["wkb_type"] = layer.wkbType()
        except Exception:
            record["wkb_type"] = None

        try:
            record["feature_count"] = layer.featureCount()
        except Exception:
            record["feature_count"] = None

        fields = []

        for field in layer.fields():

            fields.append({
                "name": field.name(),
                "type": field.typeName(),
                "length": field.length(),
                "precision": field.precision()
            })

        record["fields"] = fields


    # ========================================================
    # RASTER
    # ========================================================

    elif layer.type() == QgsMapLayerType.RasterLayer:

        record["layer_type"] = "raster"

        record["width"] = layer.width()
        record["height"] = layer.height()
        record["band_count"] = layer.bandCount()

        if layer.width() > 0 and layer.height() > 0:

            ext = layer.extent()

            record["pixel_size_x"] = (
                ext.width() / layer.width()
            )

            record["pixel_size_y"] = (
                ext.height() / layer.height()
            )

        bands = []

        provider = layer.dataProvider()

        for band in range(1, layer.bandCount() + 1):

            band_info = {
                "band": band
            }

            try:
                band_info["data_type"] = str(
                    provider.dataType(band)
                )
            except Exception:
                band_info["data_type"] = None

            band_info["nodata"] = safe_nodata(
                layer,
                band
            )

            band_info["statistics"] = safe_raster_stats(
                layer,
                band
            )

            bands.append(band_info)

        record["bands"] = bands


    # ========================================================
    # OTHER
    # ========================================================

    else:

        record["layer_type"] = "other"


    inventory.append(record)


# ============================================================
# WRITE JSON
# ============================================================

json_output = {
    "project_title": project.title(),
    "project_filename": project.fileName(),
    "project_crs": project.crs().authid(),
    "export_time": datetime.now().isoformat(),
    "layer_count": len(inventory),
    "layers": inventory
}


with open(
    JSON_PATH,
    "w",
    encoding="utf-8"
) as f:

    json.dump(
        json_output,
        f,
        indent=2,
        ensure_ascii=False,
        default=str
    )


# ============================================================
# WRITE FLAT CSV
# ============================================================

csv_rows = []

for r in inventory:

    row = {
        "name": r.get("name"),
        "group_path": r.get("group_path"),
        "layer_type": r.get("layer_type"),
        "provider": r.get("provider"),
        "source": r.get("source"),
        "crs_authid": r.get("crs_authid"),
        "crs_description": r.get("crs_description"),
        "valid": r.get("valid"),

        "geometry_type": r.get("geometry_type"),
        "wkb_type": r.get("wkb_type"),
        "feature_count": r.get("feature_count"),

        "width": r.get("width"),
        "height": r.get("height"),
        "band_count": r.get("band_count"),
        "pixel_size_x": r.get("pixel_size_x"),
        "pixel_size_y": r.get("pixel_size_y"),

        "xmin": r["extent"]["xmin"],
        "ymin": r["extent"]["ymin"],
        "xmax": r["extent"]["xmax"],
        "ymax": r["extent"]["ymax"],

        "fields": json.dumps(
            r.get("fields", []),
            ensure_ascii=False
        ),

        "bands": json.dumps(
            r.get("bands", []),
            ensure_ascii=False,
            default=str
        )
    }

    csv_rows.append(row)


fieldnames = list(csv_rows[0].keys()) if csv_rows else []


with open(
    CSV_PATH,
    "w",
    newline="",
    encoding="utf-8-sig"
) as f:

    writer = csv.DictWriter(
        f,
        fieldnames=fieldnames
    )

    writer.writeheader()
    writer.writerows(csv_rows)


print("Metadata export complete.")
print("CSV :", CSV_PATH)
print("JSON:", JSON_PATH)
print("Layers exported:", len(inventory))


