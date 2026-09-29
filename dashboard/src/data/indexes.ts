import type { DashboardData, DashboardIndexes, Id } from './types';

export function idKey(value: Id | null | undefined): string {
  return value == null ? '' : String(value);
}

function addToIndex<K, V>(index: Map<K, V[]>, key: K, value: V): void {
  const existing = index.get(key);
  if (existing) existing.push(value);
  else index.set(key, [value]);
}

export function buildIndexes(data: DashboardData): DashboardIndexes {
  const slopeById = new Map();
  const sourceBySuId = new Map();
  const pathBySuId = new Map();
  const pathBySourceId = new Map();
  const envelopeBySuId = new Map();
  const envelopeBySourceId = new Map();
  const buildingBySuId = new Map();

  data.slopes.features.forEach((feature) => slopeById.set(idKey(feature.properties.SU_ID), feature));
  data.sources.features.forEach((feature) => addToIndex(sourceBySuId, idKey(feature.properties.SU_ID), feature));
  data.paths.features.forEach((feature) => {
    addToIndex(pathBySuId, idKey(feature.properties.SU_ID), feature);
    addToIndex(pathBySourceId, idKey(feature.properties.SOURCE_ID), feature);
  });
  data.envelopes.features.forEach((feature) => {
    addToIndex(envelopeBySuId, idKey(feature.properties.SU_ID), feature);
    addToIndex(envelopeBySourceId, idKey(feature.properties.SOURCE_ID), feature);
  });
  data.buildings?.features.forEach((feature) => addToIndex(buildingBySuId, idKey(feature.properties.SU_ID), feature));

  return { slopeById, sourceBySuId, pathBySuId, pathBySourceId, envelopeBySuId, envelopeBySourceId, buildingBySuId };
}
