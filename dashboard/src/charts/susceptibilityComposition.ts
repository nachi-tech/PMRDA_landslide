import type { SlopeProperties } from '../data/types';

const classes: Array<[keyof SlopeProperties, string, string]> = [
  ['PCT_C1', 'Class 1', '#22c55e'],
  ['PCT_C2', 'Class 2', '#84cc16'],
  ['PCT_C3', 'Class 3', '#facc15'],
  ['PCT_C4', 'Class 4', '#f97316'],
  ['PCT_C5', 'Class 5', '#dc2626'],
];

export function susceptibilityComposition(properties: SlopeProperties): string {
  const segments = classes.map(([field, label, color]) => {
    const value = Number(properties[field] ?? 0);
    const width = Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0;
    return `<span title="${label}: ${width.toFixed(1)}%" style="width:${width}%;background:${color}"></span>`;
  }).join('');

  return `<div class="susceptibility-bar" aria-label="Susceptibility class composition">${segments}</div>`;
}
