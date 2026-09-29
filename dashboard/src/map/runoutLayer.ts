import L from 'leaflet';
import type { EnvelopeFeature, RunoutPathFeature } from '../data/types';
import { idKey } from '../data/indexes';
import { envelopeStyle, staticPathStyle } from './styles';

export function drawStaticPaths(layerGroup: L.LayerGroup, paths: RunoutPathFeature[]): void {
  layerGroup.clearLayers();
  paths.forEach((feature) => {
    L.geoJSON(feature, {
      style: staticPathStyle,
      onEachFeature(_, layer) {
        layer.bindTooltip(`PATH_ID: ${idKey(feature.properties.PATH_ID)}`);
      },
    }).addTo(layerGroup);
  });
}

export function drawEnvelopes(layerGroup: L.LayerGroup, envelopes: EnvelopeFeature[]): void {
  layerGroup.clearLayers();
  envelopes.forEach((feature) => {
    L.geoJSON(feature, {
      style: envelopeStyle(feature),
      onEachFeature(_, layer) {
        layer.bindTooltip(`Runout envelope SOURCE_ID: ${idKey(feature.properties.SOURCE_ID)}`);
      },
    }).addTo(layerGroup);
  });
}
