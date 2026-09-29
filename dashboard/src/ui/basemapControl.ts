import { basemaps, hasMapboxToken, type BasemapKey } from '../map/basemaps';

export function bindBasemapControl(handler: (basemap: BasemapKey) => void): void {
  const select = document.getElementById('basemap-select') as HTMLSelectElement | null;
  if (!select) return;

  Array.from(select.options).forEach((option) => {
    const basemap = basemaps[option.value as BasemapKey];
    if (basemap?.requiresToken && !hasMapboxToken()) {
      option.disabled = true;
      option.textContent = `${basemap.label} (token required)`;
    }
  });

  select.addEventListener('change', () => {
    handler(select.value as BasemapKey);
  });
}