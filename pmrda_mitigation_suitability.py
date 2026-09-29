# -*- coding: utf-8 -*-
"""
PMRDA mitigation suitability preprocessing.

This script implements the integrated dashboard plan's mitigation extension as a
transparent Level A screening model. It consumes the existing PMRDA analytical
outputs and writes slope-level mitigation suitability tables for the Leaflet app.

It does NOT modify susceptibility, source-zone, runout or exposure calculations,
and it does NOT perform geotechnical design.

Outputs:
    dashboard/public/data/mitigation_suitability.csv/json
    dashboard/public/data/mitigation_score_components.csv/json
    dashboard/public/data/mitigation_diagnostics.csv/json

The scoring assumptions are intentionally externalized in:
    mitigation_scoring_config.yaml

The current config file is YAML-compatible JSON so it can be parsed with the
Python standard library. If you convert it to full YAML later, install PyYAML or
extend load_config accordingly.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

try:
    import geopandas as gpd
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "GeoPandas is required. Run in a GIS Python environment with geopandas/fiona/pyogrio."
    ) from exc

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "dashboard" / "public" / "data"
CONFIG_PATH = ROOT / "mitigation_scoring_config.yaml"

SLOPE_PATH = DATA_DIR / "slope_units_master.gpkg"
SOURCE_PATH = DATA_DIR / "candidate_source_zones.gpkg"
ENVELOPE_PATH = DATA_DIR / "runout_envelopes.gpkg"
EXPOSURE_SUMMARY_PATH = DATA_DIR / "slope_exposure_summary.gpkg"

SLOPE_LAYER = "slope_units_master"
SOURCE_LAYER = "candidate_source_zones"
ENVELOPE_LAYER = "runout_envelopes"
EXPOSURE_SUMMARY_LAYER = "slope_exposure_summary"

POLICY_ORDER = ["jute", "shotcrete", "wall", "drainage", "geometry"]


def load_config(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"Could not parse {path}. It is currently expected to be YAML-compatible JSON. {exc}"
        ) from exc


def read_required(path: Path, layer: str):
    if not path.exists():
        raise FileNotFoundError(f"Required input not found: {path}")
    return gpd.read_file(path, layer=layer)


def read_optional(path: Path, layer: str):
    if not path.exists():
        return None
    try:
        return gpd.read_file(path, layer=layer)
    except Exception as exc:
        print(f"[WARN] Could not read optional {path}:{layer}: {exc}")
        return None


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def as_float(value: Any) -> float | None:
    if is_missing(value):
        return None
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(converted) or math.isinf(converted):
        return None
    return converted


def clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, value))


def increasing(x: float, low: float, high: float) -> float:
    if high == low:
        return 100.0 if x >= high else 0.0
    if x <= low:
        return 0.0
    if x >= high:
        return 100.0
    return 100.0 * (x - low) / (high - low)


def decreasing(x: float, low: float, high: float) -> float:
    return 100.0 - increasing(x, low, high)


def triangular(x: float, low: float, optimum: float, high: float) -> float:
    if x <= low or x >= high:
        return 0.0
    if x == optimum:
        return 100.0
    if x < optimum:
        return 100.0 * (x - low) / (optimum - low)
    return 100.0 * (high - x) / (high - optimum)


def score_factor(raw_value: Any, spec: dict[str, Any]) -> float | None:
    func = spec.get("function")
    if func == "categorical":
        if is_missing(raw_value):
            return None
        return as_float(spec.get("scores", {}).get(str(raw_value).upper()))

    x = as_float(raw_value)
    if x is None:
        return None

    if func == "increasing":
        return clamp(increasing(x, float(spec["low"]), float(spec["high"])))
    if func == "decreasing":
        return clamp(decreasing(x, float(spec["low"]), float(spec["high"])))
    if func == "triangular":
        return clamp(triangular(x, float(spec["low"]), float(spec["optimum"]), float(spec["high"])))

    raise ValueError(f"Unsupported scoring function: {func}")


def readable_value(value: Any) -> Any:
    if is_missing(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def aggregate_sources(source_gdf) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = defaultdict(dict)
    if source_gdf is None or source_gdf.empty:
        return result

    for su_id, group in source_gdf.groupby(source_gdf["SU_ID"].astype(str)):
        def max_col(col: str):
            return as_float(group[col].max()) if col in group.columns and not group[col].dropna().empty else None

        def mean_col(col: str):
            return as_float(group[col].mean()) if col in group.columns and not group[col].dropna().empty else None

        result[su_id].update({
            "SOURCE_COUNT": int(len(group)),
            "SOURCE_AREA_HA_SUM": as_float(group["AREA_HA"].sum()) if "AREA_HA" in group.columns else None,
            "SOURCE_AREA_HA_MAX": max_col("AREA_HA"),
            "SOURCE_S_P90_MAX": max_col("S_P90"),
            "SOURCE_LSI_MEAN_MAX": max_col("LSI_MEAN"),
            "REL_POS_MEAN": mean_col("REL_POS"),
            "SOURCE_ASP_CONC_MEAN": mean_col("ASP_CONC"),
        })
    return result


def aggregate_envelopes(envelope_gdf) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = defaultdict(dict)
    if envelope_gdf is None or envelope_gdf.empty:
        return result

    for su_id, group in envelope_gdf.groupby(envelope_gdf["SU_ID"].astype(str)):
        result[su_id].update({
            "N_PATHS": int(group["N_PATHS"].sum()) if "N_PATHS" in group.columns else None,
            "MAX_RUN_M": as_float(group["MAX_LEN_M"].max()) if "MAX_LEN_M" in group.columns else None,
            "MAX_DROP_M": as_float(group["MAX_DROP_M"].max()) if "MAX_DROP_M" in group.columns else None,
            "RUNOUT_AREA_HA_SUM": as_float(group["AREA_HA"].sum()) if "AREA_HA" in group.columns else None,
        })
    return result


def exposure_attrs(summary_gdf) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = defaultdict(dict)
    if summary_gdf is None or summary_gdf.empty or "SU_ID" not in summary_gdf.columns:
        return result

    fields = [
        "N_BLD_SRC", "N_BLD_RUN", "N_BLD_BOTH", "N_BLD_TOT",
        "BLD_FP_M2", "SRC_OV_M2", "RUN_OV_M2", "TOT_OV_M2",
        "MEAN_OV_P", "MAX_OV_P", "MAX_RUN_M", "MAX_DROP_M",
    ]
    for _, row in summary_gdf.iterrows():
        su_id = str(row["SU_ID"])
        for field in fields:
            if field in summary_gdf.columns:
                result[su_id][field] = readable_value(row[field])
    return result


def build_slope_records(slopes, sources, envelopes, exposure_summary) -> dict[str, dict[str, Any]]:
    source_by_su = aggregate_sources(sources)
    envelope_by_su = aggregate_envelopes(envelopes)
    exposure_by_su = exposure_attrs(exposure_summary)

    records: dict[str, dict[str, Any]] = {}
    for _, row in slopes.iterrows():
        su_id = str(row["SU_ID"])
        record: dict[str, Any] = {}
        for col in slopes.columns:
            if col != slopes.geometry.name:
                record[col] = readable_value(row[col])
        record.update(source_by_su.get(su_id, {}))
        record.update(envelope_by_su.get(su_id, {}))
        record.update(exposure_by_su.get(su_id, {}))

        # Level A hydrology/constructability placeholders. These are deliberately
        # left missing unless a future derivation step populates them.
        for missing_field in [
            "FLOWACC_P90", "TWI_P90", "DIST_TO_CHANNEL", "SEEPAGE_PRESENT",
            "MATERIAL_CLASS", "VEG_COVER_PCT", "DIST_BUILDING",
            "TOE_CONSTRUCTABILITY", "AVAILABLE_TOE_SPACE",
        ]:
            record.setdefault(missing_field, None)
        records[su_id] = record
    return records


def calculate_policy(policy_key: str, policy: dict[str, Any], record: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    components: list[dict[str, Any]] = []
    weighted_sum = 0.0
    available_weight = 0.0
    total_weight = 0.0
    missing_factors: list[str] = []
    positive_reasons: list[str] = []
    limitations: list[str] = []

    for factor_key, spec in policy.get("factors", {}).items():
        weight = float(spec.get("weight", 0))
        total_weight += weight
        raw_value = record.get(factor_key)
        factor_score = score_factor(raw_value, spec)
        contribution = None
        status = "missing" if factor_score is None else "available"

        if factor_score is not None:
            available_weight += weight
            contribution = factor_score * weight
            weighted_sum += contribution
            if factor_score >= 70:
                positive_reasons.append(str(spec.get("positive", f"{factor_key} supports this policy.")))
            elif factor_score <= 35:
                limitations.append(str(spec.get("negative", f"{factor_key} limits this policy.")))
        else:
            missing_factors.append(factor_key)
            limitations.append(f"Missing {spec.get('label', factor_key)} ({spec.get('data_source', 'input data')}).")

        components.append({
            "SU_ID": record["SU_ID"],
            "POLICY": policy_key,
            "POLICY_LABEL": policy.get("label", policy_key),
            "FACTOR": factor_key,
            "FACTOR_LABEL": spec.get("label", factor_key),
            "RAW_VALUE": readable_value(raw_value),
            "FACTOR_SCORE": None if factor_score is None else round(factor_score, 2),
            "WEIGHT": weight,
            "CONTRIBUTION": None if contribution is None else round(contribution, 2),
            "DATA_SOURCE": spec.get("data_source", "PMRDA analytical output"),
            "STATUS": status,
            "POSITIVE_TEXT": spec.get("positive"),
            "LIMITATION_TEXT": spec.get("negative"),
        })

    suitability = weighted_sum / available_weight if available_weight else None
    confidence = (available_weight / total_weight * 100.0) if total_weight else 0.0
    confidence_cap_reasons: list[str] = []

    for field, cap in policy.get("confidence_caps", {}).items():
        if is_missing(record.get(field)) and confidence > float(cap):
            confidence = float(cap)
            confidence_cap_reasons.append(f"{field} unavailable; confidence capped at {cap}%.")

    if suitability is None:
        suitability = 0.0

    positive_reasons = positive_reasons[:3] or ["No strong positive factor is available under current Level A data."]
    limitations = (confidence_cap_reasons + limitations)[:4] or ["No major limitation identified in available Level A data."]

    summary = {
        "SU_ID": record["SU_ID"],
        "POLICY": policy_key,
        "POLICY_LABEL": policy.get("label", policy_key),
        "SHORT_LABEL": policy.get("short_label", policy.get("label", policy_key)),
        "SCORE": round(float(suitability), 2),
        "CONFIDENCE": round(float(confidence), 2),
        "AVAILABLE_WEIGHT": round(available_weight, 3),
        "TOTAL_WEIGHT": round(total_weight, 3),
        "MISSING_FACTORS": missing_factors,
        "REASON_1": positive_reasons[0] if len(positive_reasons) > 0 else None,
        "REASON_2": positive_reasons[1] if len(positive_reasons) > 1 else None,
        "LIMITATION": limitations[0] if limitations else None,
        "REASONS": positive_reasons,
        "LIMITATIONS": limitations,
        "CONFIDENCE_CAPS": confidence_cap_reasons,
    }
    return summary, components


def calculate_priority(config: dict[str, Any], record: dict[str, Any]) -> float:
    priority_config = config.get("priority", {}).get("factors", {})
    weighted_sum = 0.0
    available_weight = 0.0
    for factor_key, spec in priority_config.items():
        raw_value = record.get(factor_key)
        if raw_value is None and factor_key == "N_BLD_TOT":
            raw_value = 0
        factor_score = score_factor(raw_value, spec)
        if factor_score is not None:
            weight = float(spec.get("weight", 0))
            available_weight += weight
            weighted_sum += factor_score * weight
    return round(weighted_sum / available_weight, 2) if available_weight else 0.0


def rank_and_annotate(summaries: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    by_su: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in summaries:
        by_su[str(row["SU_ID"])].append(row)

    close_delta = float(config.get("close_score_delta", 10))
    complementary_rules = config.get("complementary_rules", [])

    annotated: list[dict[str, Any]] = []
    for su_id, rows in by_su.items():
        rows.sort(key=lambda item: item["SCORE"], reverse=True)
        top_score = rows[0]["SCORE"] if rows else 0.0
        close = [row["POLICY"] for row in rows if top_score - row["SCORE"] <= close_delta]
        score_lookup = {row["POLICY"]: row["SCORE"] for row in rows}
        complementary = []
        for rule in complementary_rules:
            thresholds = rule.get("if", {})
            if all(score_lookup.get(policy, 0) >= threshold for policy, threshold in thresholds.items()):
                complementary.append(rule.get("label"))

        for rank, row in enumerate(rows, start=1):
            row["RANK"] = rank
            row["IS_LEADING_CANDIDATE"] = row["POLICY"] in close
            row["LEADING_CANDIDATES"] = close
            row["COMPLEMENTARY_MEASURES"] = [item for item in complementary if item]
            if len(close) > 1:
                row["RANK_NOTE"] = "Close scores: show multiple leading screening options rather than forcing one winner."
            else:
                row["RANK_NOTE"] = "Highest screening suitability for this slope under current assumptions."
            annotated.append(row)
    return annotated


def sensitivity_class(rows: list[dict[str, Any]]) -> str:
    top = sorted(rows, key=lambda item: item["SCORE"], reverse=True)
    if not top:
        return "DATA-LIMITED"
    if top[0]["CONFIDENCE"] < 45:
        return "DATA-LIMITED"
    if len(top) > 1 and top[0]["SCORE"] - top[1]["SCORE"] <= 8:
        return "SENSITIVE"
    return "ROBUST"


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"[OK] Wrote {path}")


def write_json(path: Path, data: Any, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        payload = json.dumps(data, separators=(",", ":"), default=str)
    else:
        payload = json.dumps(data, indent=2, default=str)
    path.write_text(payload, encoding="utf-8")
    print(f"[OK] Wrote {path}")


def build_outputs(records: dict[str, dict[str, Any]], config: dict[str, Any], sample_ids: set[str] | None = None):
    policies = config["policies"]
    summaries: list[dict[str, Any]] = []
    components: list[dict[str, Any]] = []

    selected_items = records.items()
    if sample_ids is not None:
        selected_items = [(su_id, record) for su_id, record in records.items() if su_id in sample_ids]

    for su_id, record in selected_items:
        record["SU_ID"] = readable_value(record.get("SU_ID", su_id))
        priority = calculate_priority(config, record)
        for policy_key in POLICY_ORDER:
            summary, component_rows = calculate_policy(policy_key, policies[policy_key], record)
            summary["PRIORITY_SCORE"] = priority
            summary["PRIORITY_CLASS"] = "HIGH" if priority >= 65 else "MEDIUM" if priority >= 35 else "LOW"
            summaries.append(summary)
            components.extend(component_rows)

    summaries = rank_and_annotate(summaries, config)

    by_su = defaultdict(list)
    for row in summaries:
        by_su[str(row["SU_ID"])].append(row)
    for su_id, rows in by_su.items():
        sens = sensitivity_class(rows)
        for row in rows:
            row["SENSITIVITY_CLASS"] = sens
            if sens == "SENSITIVE":
                row["SENSITIVITY_NOTE"] = "Recommendation sensitive to scoring assumptions or close competing scores."
            elif sens == "DATA-LIMITED":
                row["SENSITIVITY_NOTE"] = "Data-limited screening result; field verification is required."
            else:
                row["SENSITIVITY_NOTE"] = "Current top option is relatively separated under base assumptions."

    return summaries, components


def diagnostics(summaries: list[dict[str, Any]], components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    by_policy = defaultdict(list)
    for row in summaries:
        by_policy[row["POLICY"]].append(row)

    top_counts = Counter(row["POLICY"] for row in summaries if row.get("RANK") == 1)
    close_slope_count = len({str(row["SU_ID"]) for row in summaries if len(row.get("LEADING_CANDIDATES", [])) > 1})
    no_confident_count = len({str(row["SU_ID"]) for row in summaries if row.get("RANK") == 1 and row.get("CONFIDENCE", 0) < 50})

    for policy, policy_rows in by_policy.items():
        scores = [float(row["SCORE"]) for row in policy_rows]
        confs = [float(row["CONFIDENCE"]) for row in policy_rows]
        rows.append({
            "METRIC": "policy_summary",
            "POLICY": policy,
            "COUNT": len(policy_rows),
            "MEAN_SCORE": round(mean(scores), 2) if scores else None,
            "MIN_SCORE": round(min(scores), 2) if scores else None,
            "MAX_SCORE": round(max(scores), 2) if scores else None,
            "MEAN_CONFIDENCE": round(mean(confs), 2) if confs else None,
            "TOP_POLICY_COUNT": top_counts.get(policy, 0),
            "CLOSE_SCORE_SLOPE_COUNT": close_slope_count,
            "NO_CONFIDENT_INTERVENTION_COUNT": no_confident_count,
        })

    missing_by_factor = Counter(row["FACTOR"] for row in components if row["STATUS"] == "missing")
    for factor, count in missing_by_factor.most_common():
        rows.append({
            "METRIC": "missing_factor",
            "POLICY": "ALL",
            "FACTOR": factor,
            "COUNT": count,
        })
    return rows


def nested_json(summaries: list[dict[str, Any]], components: list[dict[str, Any]]) -> dict[str, Any]:
    components_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for component in components:
        components_by_key[(str(component["SU_ID"]), component["POLICY"])].append(component)

    result: dict[str, Any] = {}
    for summary in summaries:
        su_id = str(summary["SU_ID"])
        result.setdefault(su_id, {"SU_ID": summary["SU_ID"], "policies": []})
        item = dict(summary)
        item["components"] = components_by_key[(su_id, summary["POLICY"])]
        result[su_id]["policies"].append(item)

    for su_id in result:
        result[su_id]["policies"].sort(key=lambda item: item["RANK"])
    return result


def build_mitigation_index(slope_details: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Build a compact startup index for the browser.

    The full per-factor explanations are intentionally excluded because loading
    them for all slope units creates very large JSON files. The dashboard fetches
    detailed records lazily from mitigation/by_su/{SU_ID}.json.
    """
    index: dict[str, Any] = {}
    for su_id, detail in slope_details.items():
        policies = sorted(detail.get("policies", []), key=lambda row: row.get("RANK", 999))
        if not policies:
            continue
        top = policies[0]
        index[su_id] = {
            "SU_ID": detail.get("SU_ID", su_id),
            "top_policy": top.get("POLICY"),
            "top_label": top.get("SHORT_LABEL", top.get("POLICY_LABEL")),
            "top_score": top.get("SCORE"),
            "top_confidence": top.get("CONFIDENCE"),
            "priority_score": top.get("PRIORITY_SCORE"),
            "priority_class": top.get("PRIORITY_CLASS"),
            "sensitivity_class": top.get("SENSITIVITY_CLASS"),
            "policy_scores": {policy.get("POLICY"): policy.get("SCORE") for policy in policies},
            "leading_candidates": top.get("LEADING_CANDIDATES", []),
            "detail_path": f"/data/mitigation/by_su/{su_id}.json",
        }
    return index


def write_mitigation_detail_files(slope_details: dict[str, dict[str, Any]]) -> None:
    detail_dir = OUT_DIR / "mitigation" / "by_su"
    detail_dir.mkdir(parents=True, exist_ok=True)
    stale_count = 0
    for old_file in detail_dir.glob("*.json"):
        old_file.unlink()
        stale_count += 1
    if stale_count:
        print(f"[OK] Removed {stale_count:,} stale mitigation detail files from {detail_dir}")

    for su_id, detail in slope_details.items():
        compact_path = detail_dir / f"{su_id}.json"
        compact_path.write_text(json.dumps(detail, separators=(",", ":"), default=str), encoding="utf-8")
    print(f"[OK] Wrote {len(slope_details):,} mitigation detail files -> {detail_dir}")


def remove_stale_large_qa_files() -> None:
    for name in ["mitigation_suitability.csv", "mitigation_score_components.csv"]:
        path = OUT_DIR / name
        if path.exists():
            path.unlink()
            print(f"[OK] Removed stale large QA file {path}")


def parse_args(argv: Iterable[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-size", type=int, default=None, help="Limit outputs to the first N slope units.")
    parser.add_argument("--su-ids", nargs="*", default=None, help="Explicit SU_ID values to include.")
    parser.add_argument("--write-csv", action="store_true", help="Also write large CSV QA tables. Disabled by default for full dashboard exports.")
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> None:
    args = parse_args(argv)
    config = load_config(CONFIG_PATH)

    slopes = read_required(SLOPE_PATH, SLOPE_LAYER)
    sources = read_required(SOURCE_PATH, SOURCE_LAYER)
    envelopes = read_required(ENVELOPE_PATH, ENVELOPE_LAYER)
    exposure_summary = read_optional(EXPOSURE_SUMMARY_PATH, EXPOSURE_SUMMARY_LAYER)

    if "SU_ID" not in slopes.columns:
        raise ValueError("slope_units_master must contain SU_ID")

    records = build_slope_records(slopes, sources, envelopes, exposure_summary)
    all_ids = list(records.keys())
    sample_ids = set(map(str, args.su_ids)) if args.su_ids else None
    if sample_ids is None and args.sample_size:
        sample_ids = set(all_ids[: args.sample_size])

    summaries, components = build_outputs(records, config, sample_ids)
    diag = diagnostics(summaries, components)

    summary_fields = [
        "SU_ID", "POLICY", "POLICY_LABEL", "SHORT_LABEL", "SCORE", "CONFIDENCE", "RANK",
        "IS_LEADING_CANDIDATE", "PRIORITY_SCORE", "PRIORITY_CLASS", "SENSITIVITY_CLASS",
        "REASON_1", "REASON_2", "LIMITATION", "MISSING_FACTORS", "CONFIDENCE_CAPS",
        "LEADING_CANDIDATES", "COMPLEMENTARY_MEASURES", "RANK_NOTE", "SENSITIVITY_NOTE",
        "AVAILABLE_WEIGHT", "TOTAL_WEIGHT",
    ]
    component_fields = [
        "SU_ID", "POLICY", "POLICY_LABEL", "FACTOR", "FACTOR_LABEL", "RAW_VALUE",
        "FACTOR_SCORE", "WEIGHT", "CONTRIBUTION", "DATA_SOURCE", "STATUS",
        "POSITIVE_TEXT", "LIMITATION_TEXT",
    ]
    diagnostic_fields = [
        "METRIC", "POLICY", "FACTOR", "COUNT", "MEAN_SCORE", "MIN_SCORE", "MAX_SCORE",
        "MEAN_CONFIDENCE", "TOP_POLICY_COUNT", "CLOSE_SCORE_SLOPE_COUNT",
        "NO_CONFIDENT_INTERVENTION_COUNT",
    ]

    if args.write_csv:
        write_csv(OUT_DIR / "mitigation_suitability.csv", summaries, summary_fields)
        write_csv(OUT_DIR / "mitigation_score_components.csv", components, component_fields)
        write_csv(OUT_DIR / "mitigation_diagnostics.csv", diag, diagnostic_fields)
    else:
        remove_stale_large_qa_files()
        write_csv(OUT_DIR / "mitigation_diagnostics.csv", diag, diagnostic_fields)

    slope_details = nested_json(summaries, components)
    write_json(OUT_DIR / "mitigation_index.json", build_mitigation_index(slope_details), compact=True)
    write_mitigation_detail_files(slope_details)

    # Browser compatibility stubs. Older dashboard code tried to eagerly load
    # these full files, which can exceed hundreds of MB for the full inventory.
    # Keep small, valid JSON files here so stale references fail safely.
    write_json(OUT_DIR / "mitigation_suitability.json", {
        "deprecated": True,
        "replacement": "/data/mitigation_index.json plus /data/mitigation/by_su/{SU_ID}.json",
        "record_count": len(slope_details),
    }, compact=True)
    write_json(OUT_DIR / "mitigation_score_components.json", {
        "deprecated": True,
        "replacement": "Per-slope components are embedded in /data/mitigation/by_su/{SU_ID}.json",
        "component_count": len(components),
    }, compact=True)
    write_json(OUT_DIR / "mitigation_diagnostics.json", {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "diagnostics": diag,
        "disclaimer": "Mitigation screening, not engineering design. Suitability percentages represent match to transparent screening criteria; they are not probability of success, expected risk reduction, structural adequacy or construction recommendation.",
        "sample_export": sample_ids is not None,
        "browser_loading": "Dashboard loads mitigation_index.json at startup and fetches per-slope detail JSON on selection.",
    })


if __name__ == "__main__":
    main()
