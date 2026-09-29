import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { basemaps, type BasemapKey } from './basemaps';

export interface DashboardMap {
  map: L.Map;
  basemapLayer: L.TileLayer;
}

export function createMap(containerId: string, initialBasemap: BasemapKey = 'osm'): DashboardMap {
  const map = L.map(containerId, {
    zoomControl: true,
    preferCanvas: true,
  }).setView([18.75, 73.55], 10);

  const basemapLayer = basemaps[initialBasemap].createLayer().addTo(map);

  return { map, basemapLayer };
}
