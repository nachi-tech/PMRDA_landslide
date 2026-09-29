import L from 'leaflet';
import type { DashboardData } from '../data/types';
import { ridgeStyle } from './styles';

export interface LayerManager {
  slopeLayer: L.GeoJSON;
  selectedSlopeLayer: L.LayerGroup;
  sourceLayer: L.LayerGroup;
  staticPathLayer: L.LayerGroup;
  animationLayer: L.LayerGroup;
  envelopeLayer: L.LayerGroup;
  buildingLayer: L.LayerGroup;
  ridgeLayer: L.GeoJSON | null;
  clearSelectionLayers: () => void;
}

export function createLayerManager(map: L.Map, data: DashboardData): LayerManager {
  const selectedSlopeLayer = L.layerGroup().addTo(map);
  const sourceLayer = L.layerGroup().addTo(map);
  const staticPathLayer = L.layerGroup().addTo(map);
  const animationLayer = L.layerGroup().addTo(map);
  const envelopeLayer = L.layerGroup().addTo(map);
  const buildingLayer = L.layerGroup().addTo(map);
  const slopeLayer = L.geoJSON(undefined).addTo(map);
  const ridgeLayer = data.ridges ? L.geoJSON(data.ridges, { style: ridgeStyle }).addTo(map) : null;

  return {
    slopeLayer,
    selectedSlopeLayer,
    sourceLayer,
    staticPathLayer,
    animationLayer,
    envelopeLayer,
    buildingLayer,
    ridgeLayer,
    clearSelectionLayers() {
      selectedSlopeLayer.clearLayers();
      sourceLayer.clearLayers();
      staticPathLayer.clearLayers();
      animationLayer.clearLayers();
      envelopeLayer.clearLayers();
      buildingLayer.clearLayers();
    },
  };
}

export function setLayerVisible(map: L.Map, layer: L.Layer | null, visible: boolean): void {
  if (!layer) return;
  if (visible && !map.hasLayer(layer)) layer.addTo(map);
  if (!visible && map.hasLayer(layer)) layer.removeFrom(map);
}
