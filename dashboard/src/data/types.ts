import type { Feature, FeatureCollection, Geometry, LineString, MultiLineString, MultiPolygon, Polygon } from 'geojson';

export type Id = string | number;

export interface SlopeProperties {
  SU_ID: Id;
  AREA_HA?: number | null;
  S_MEAN?: number | null;
  S_P75?: number | null;
  S_P90?: number | null;
  S_MAX?: number | null;
  ASP_MEAN?: number | null;
  ASP_CONC?: number | null;
  Z_MIN?: number | null;
  Z_MAX?: number | null;
  RELIEF?: number | null;
  CURV_MEAN?: number | null;
  MEAN_LSI?: number | null;
  PCT_C1?: number | null;
  PCT_C2?: number | null;
  PCT_C3?: number | null;
  PCT_C4?: number | null;
  PCT_C5?: number | null;
  HIGH_PCT?: number | null;
  DOM_CLASS?: Id | null;
  N_BLD_SRC?: number | null;
  N_BLD_RUN?: number | null;
  N_BLD_BOTH?: number | null;
  N_BLD_TOT?: number | null;
  BLD_FP_M2?: number | null;
  SRC_OV_M2?: number | null;
  RUN_OV_M2?: number | null;
  TOT_OV_M2?: number | null;
  MEAN_OV_P?: number | null;
  MAX_OV_P?: number | null;
  MAX_RUN_M?: number | null;
  MAX_DROP_M?: number | null;
}

export interface SourceProperties {
  SOURCE_ID: Id;
  SU_ID: Id;
  AREA_M2?: number | null;
  AREA_HA?: number | null;
  S_MEAN?: number | null;
  S_P90?: number | null;
  LSI_MEAN?: number | null;
  Z_MIN?: number | null;
  Z_MAX?: number | null;
  Z_MEAN?: number | null;
  RELIEF?: number | null;
  ASP_MEAN?: number | null;
  ASP_CONC?: number | null;
  REL_POS?: number | null;
}

export interface RunoutPathProperties {
  PATH_ID: Id;
  SOURCE_ID: Id;
  SU_ID: Id;
  START_Z?: number | null;
  END_Z?: number | null;
  DROP_M?: number | null;
  LENGTH_M?: number | null;
  REACH_DEG?: number | null;
  N_STEPS?: number | null;
  END_REASON?: string | null;
}

export interface EnvelopeProperties {
  SOURCE_ID: Id;
  SU_ID: Id;
  N_PATHS?: number | null;
  MAX_LEN_M?: number | null;
  MAX_DROP_M?: number | null;
  AREA_HA?: number | null;
}

export interface BuildingProperties {
  BUILD_FID: Id;
  SU_ID: Id;
  EXP_TYPE?: 'SOURCE' | 'RUNOUT' | 'BOTH' | string | null;
  BLDG_AREA?: number | null;
  SRC_OVLP?: number | null;
  RUN_OVLP?: number | null;
  TOT_OVLP?: number | null;
  OVLP_PCT?: number | null;
}

export interface ModelMetadata {
  generated_at?: string;
  analysis_crs?: string;
  display_crs?: string;
  source?: string;
  scenario_parameters?: Record<string, unknown>;
  warnings?: string[];
  export_counts?: Record<string, number>;
  sample_export?: boolean;
}

export interface MitigationComponent {
  SU_ID: Id;
  POLICY: string;
  POLICY_LABEL: string;
  FACTOR: string;
  FACTOR_LABEL: string;
  RAW_VALUE: string | number | boolean | null;
  FACTOR_SCORE: number | null;
  WEIGHT: number;
  CONTRIBUTION: number | null;
  DATA_SOURCE: string;
  STATUS: 'available' | 'missing' | string;
  POSITIVE_TEXT?: string | null;
  LIMITATION_TEXT?: string | null;
}

export interface MitigationPolicyScore {
  SU_ID: Id;
  POLICY: string;
  POLICY_LABEL: string;
  SHORT_LABEL: string;
  SCORE: number;
  CONFIDENCE: number;
  RANK: number;
  IS_LEADING_CANDIDATE: boolean;
  PRIORITY_SCORE: number;
  PRIORITY_CLASS: 'LOW' | 'MEDIUM' | 'HIGH' | string;
  SENSITIVITY_CLASS: 'ROBUST' | 'SENSITIVE' | 'DATA-LIMITED' | string;
  REASON_1?: string | null;
  REASON_2?: string | null;
  LIMITATION?: string | null;
  REASONS?: string[];
  LIMITATIONS?: string[];
  MISSING_FACTORS?: string[];
  CONFIDENCE_CAPS?: string[];
  LEADING_CANDIDATES?: string[];
  COMPLEMENTARY_MEASURES?: string[];
  RANK_NOTE?: string;
  SENSITIVITY_NOTE?: string;
  AVAILABLE_WEIGHT?: number;
  TOTAL_WEIGHT?: number;
  components: MitigationComponent[];
}

export interface MitigationSlopeResult {
  SU_ID: Id;
  policies: MitigationPolicyScore[];
}

export interface MitigationIndexEntry {
  SU_ID: Id;
  top_policy?: string;
  top_policy_label?: string;
  top_label?: string;
  top_score?: number;
  top_confidence?: number;
  priority_score?: number;
  priority_class?: 'LOW' | 'MEDIUM' | 'HIGH' | string;
  sensitivity_class?: 'ROBUST' | 'SENSITIVE' | 'DATA-LIMITED' | string;
  policy_scores?: Record<string, number>;
  policy_confidence?: Record<string, number>;
  leading_candidates?: string[];
  detail_path?: string;
}

export type MitigationIndex = Record<string, MitigationIndexEntry>;

export interface ExportValidationReport {
  generated_at?: string;
  status?: 'pass' | 'fail' | string;
  sample_export?: boolean;
  analysis_crs_expected?: string;
  display_crs_expected?: string;
  layers?: Record<string, {
    present?: boolean;
    feature_count?: number;
    crs?: string | null;
    geometry_types?: Record<string, number>;
    bounds?: number[] | null;
    missing_required_fields?: string[];
    null_required_fields?: Record<string, number>;
    invalid_geometry_count?: number;
    empty_geometry_count?: number;
  }>;
  joins?: Record<string, number>;
  errors?: string[];
  warnings?: string[];
}

export type SlopeFeature = Feature<Polygon | MultiPolygon, SlopeProperties>;
export type SourceFeature = Feature<Polygon | MultiPolygon, SourceProperties>;
export type RunoutPathFeature = Feature<LineString | MultiLineString, RunoutPathProperties>;
export type EnvelopeFeature = Feature<Polygon | MultiPolygon, EnvelopeProperties>;
export type BuildingFeature = Feature<Polygon | MultiPolygon, BuildingProperties>;
export type AnyFeature = Feature<Geometry, Record<string, unknown>>;

export interface DashboardData {
  slopes: FeatureCollection<Polygon | MultiPolygon, SlopeProperties>;
  sources: FeatureCollection<Polygon | MultiPolygon, SourceProperties>;
  paths: FeatureCollection<LineString | MultiLineString, RunoutPathProperties>;
  envelopes: FeatureCollection<Polygon | MultiPolygon, EnvelopeProperties>;
  buildings: FeatureCollection<Polygon | MultiPolygon, BuildingProperties> | null;
  ridges: FeatureCollection<Geometry, Record<string, unknown>> | null;
  slopeSummary: Record<string, unknown> | null;
  metadata: ModelMetadata;
  mitigationIndex: MitigationIndex | null;
  validation: ExportValidationReport | null;
}

export interface DashboardIndexes {
  slopeById: Map<string, SlopeFeature>;
  sourceBySuId: Map<string, SourceFeature[]>;
  pathBySuId: Map<string, RunoutPathFeature[]>;
  pathBySourceId: Map<string, RunoutPathFeature[]>;
  envelopeBySuId: Map<string, EnvelopeFeature[]>;
  envelopeBySourceId: Map<string, EnvelopeFeature[]>;
  buildingBySuId: Map<string, BuildingFeature[]>;
}

export interface DashboardState {
  selectedSuId: string | null;
  selectedSourceId: string | null;
  selectedScenario: string;
  animationStatus: 'idle' | 'playing' | 'paused' | 'complete';
  animationSpeed: number;
  technicalMode: boolean;
  visibleLayers: {
    slopes: boolean;
    sources: boolean;
    paths: boolean;
    envelopes: boolean;
    buildings: boolean;
    ridges: boolean;
  };
}
