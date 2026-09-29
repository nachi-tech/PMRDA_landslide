import L from 'leaflet';
import type { BuildingFeature } from '../data/types';
import { buildingStyle } from './styles';

function formatNumber(value: unknown, digits = 1): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—';
}

export function drawBuildings(layerGroup: L.LayerGroup, buildings: BuildingFeature[]): void {
  layerGroup.clearLayers();
  buildings.forEach((feature) => {
    L.geoJSON(feature, {
      style: buildingStyle(feature),
      onEachFeature(_, layer) {
        const p = feature.properties;
        layer.bindPopup(`
          <strong>Building exposure</strong><br />
          BUILD_FID: ${p.BUILD_FID}<br />
          Exposure type: ${p.EXP_TYPE ?? '—'}<br />
          Building footprint: ${formatNumber(p.BLDG_AREA)} m²<br />
          Source overlap: ${formatNumber(p.SRC_OVLP)} m²<br />
          Runout overlap: ${formatNumber(p.RUN_OVLP)} m²<br />
          Total overlap: ${formatNumber(p.TOT_OVLP)} m²<br />
          Overlap: ${formatNumber(p.OVLP_PCT, 2)}%<br />
          SU_ID: ${p.SU_ID}<br />
          <em>Building intersects modelled source/runout screening geometry.</em>
        `);
      },
    }).addTo(layerGroup);
  });
}
