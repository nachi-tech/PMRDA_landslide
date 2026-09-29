import L from 'leaflet';

export type BasemapKey = 'osm' | 'cartoLight' | 'cartoDark' | 'mapboxStreets' | 'mapboxSatellite';

export interface BasemapDefinition {
  label: string;
  requiresToken?: boolean;
  createLayer: () => L.TileLayer;
}

const mapboxToken = import.meta.env.VITE_MAPBOX_TOKEN;

function requireMapboxToken(): string {
  if (!mapboxToken) {
    throw new Error('Missing VITE_MAPBOX_TOKEN. Configure a Mapbox public token or choose a non-Mapbox basemap.');
  }
  return mapboxToken;
}

export const basemaps: Record<BasemapKey, BasemapDefinition> = {
  osm: {
    label: 'OpenStreetMap',
    createLayer: () =>
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '&copy; OpenStreetMap contributors',
      }),
  },
  cartoLight: {
    label: 'Carto Light',
    createLayer: () =>
      L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
        maxZoom: 20,
        attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
      }),
  },
  cartoDark: {
    label: 'Carto Dark',
    createLayer: () =>
      L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        maxZoom: 20,
        attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
      }),
  },
  mapboxStreets: {
    label: 'Mapbox Streets',
    requiresToken: true,
    createLayer: () => {
      const token = requireMapboxToken();
      return L.tileLayer(`https://api.mapbox.com/styles/v1/mapbox/streets-v12/tiles/512/{z}/{x}/{y}@2x?access_token=${token}`, {
        tileSize: 512,
        zoomOffset: -1,
        maxZoom: 22,
        attribution: '&copy; Mapbox &copy; OpenStreetMap contributors',
      });
    },
  },
  mapboxSatellite: {
    label: 'Mapbox Satellite',
    requiresToken: true,
    createLayer: () => {
      const token = requireMapboxToken();
      return L.tileLayer(`https://api.mapbox.com/v4/mapbox.satellite/{z}/{x}/{y}@2x.jpg90?access_token=${token}`, {
        maxZoom: 22,
        attribution: '&copy; Mapbox &copy; OpenStreetMap contributors',
      });
    },
  },
};

export function hasMapboxToken(): boolean {
  return Boolean(mapboxToken);
}