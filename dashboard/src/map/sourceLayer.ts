import L from 'leaflet';
import type { SourceFeature } from '../data/types';
import { idKey } from '../data/indexes';
import { sourceFadedStyle, sourceStyle } from './styles';

export function drawSources(
  layerGroup: L.LayerGroup,
  sources: SourceFeature[],
  selectedSourceId: string | null,
  onSelectSource: (sourceId: string) => void,
): void {
  layerGroup.clearLayers();
  sources.forEach((feature) => {
    const sourceId = idKey(feature.properties.SOURCE_ID);
    const style = selectedSourceId && selectedSourceId !== sourceId ? sourceFadedStyle : sourceStyle(feature);
    L.geoJSON(feature, {
      style,
      onEachFeature(_, layer) {
        layer.on('click', () => onSelectSource(sourceId));
        layer.bindTooltip(`SOURCE_ID: ${sourceId}`);
      },
    }).addTo(layerGroup);
  });
}
