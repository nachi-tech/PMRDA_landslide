import type { DashboardData, ExportValidationReport, MitigationIndex, MitigationSlopeResult, ModelMetadata } from './types';

async function fetchJson<T>(path: string, required = true): Promise<T | null> {
  const response = await fetch(path);
  if (!response.ok) {
    if (!required && response.status === 404) return null;
    throw new Error(`Could not load ${path}: ${response.status} ${response.statusText}`);
  }
  try {
    return (await response.json()) as T;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`Could not parse ${path}: ${message}`);
  }
}

export async function loadDashboardData(): Promise<DashboardData> {
  const [slopes, sources, paths, envelopes, buildings, ridges, slopeSummary, metadata, mitigationIndex, validation] = await Promise.all([
    fetchJson<DashboardData['slopes']>('/data/slope_units.geojson'),
    fetchJson<DashboardData['sources']>('/data/source_zones.geojson'),
    fetchJson<DashboardData['paths']>('/data/runout_paths.geojson'),
    fetchJson<DashboardData['envelopes']>('/data/runout_envelopes.geojson'),
    fetchJson<DashboardData['buildings']>('/data/exposed_buildings.geojson', false),
    fetchJson<DashboardData['ridges']>('/data/ridges.geojson', false),
    fetchJson<Record<string, unknown>>('/data/slope_summary.json', false),
    fetchJson<ModelMetadata>('/data/model_metadata.json', false),
    fetchJson<MitigationIndex>('/data/mitigation_index.json', false),
    fetchJson<ExportValidationReport>('/data/dashboard_export_validation.json', false),
  ]);

  return {
    slopes: slopes!,
    sources: sources!,
    paths: paths!,
    envelopes: envelopes!,
    buildings,
    ridges,
    slopeSummary,
    metadata: metadata ?? {},
    mitigationIndex,
    validation,
  };
}

export async function loadMitigationDetail(suId: string, detailPath?: string): Promise<MitigationSlopeResult | null> {
  const encodedId = encodeURIComponent(suId);
  return fetchJson<MitigationSlopeResult>(detailPath ?? `/data/mitigation/by_su/${encodedId}.json`, false);
}
