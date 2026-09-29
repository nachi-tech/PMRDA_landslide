import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

export function createMap(containerId: string): L.Map {
  const map = L.map(containerId, {
    zoomControl: true,
    preferCanvas: true,
  }).setView([18.75, 73.55], 10);

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap contributors',
  }).addTo(map);

  return map;
}
