import L from 'leaflet';
import type { Feature } from 'geojson';
import type { SlopeFeature, SlopeProperties } from '../data/types';
import { idKey } from '../data/indexes';
import { slopeDefaultStyle, slopeFadedStyle, slopeHoverStyle, slopeSelectedStyle } from './styles';

export function createSlopeLayer(
  features: SlopeFeature[],
  onSelect: (suId: string, feature: SlopeFeature, layer: L.Layer) => void,
): L.GeoJSON {
  const geoJson = L.geoJSON(features, {
    style: slopeDefaultStyle,
    onEachFeature(feature: Feature, layer: L.Layer) {
      const typed = feature as SlopeFeature;
      const suId = idKey(typed.properties.SU_ID);
      layer.on({
        click: () => onSelect(suId, typed, layer),
        mouseover: () => {
          if (layer instanceof L.Path) layer.setStyle(slopeHoverStyle);
          layer.bindTooltip(`SU_ID: ${suId}`, { sticky: true }).openTooltip();
        },
        mouseout: () => {
          if (layer instanceof L.Path) layer.setStyle(slopeDefaultStyle);
          layer.closeTooltip();
        },
      });
    },
  });
  return geoJson;
}

export function restyleSlopes(layer: L.GeoJSON, selectedSuId: string | null): void {
  layer.eachLayer((child) => {
    const feature = (child as L.Layer & { feature?: Feature }).feature as Feature | undefined;
    const properties = feature?.properties as SlopeProperties | undefined;
    if (!(child instanceof L.Path)) return;
    if (!selectedSuId) child.setStyle(slopeDefaultStyle);
    else if (idKey(properties?.SU_ID) === selectedSuId) child.setStyle(slopeSelectedStyle);
    else child.setStyle(slopeFadedStyle);
  });
}
