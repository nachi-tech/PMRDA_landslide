import type { PathOptions } from 'leaflet';
import type { BuildingFeature, EnvelopeFeature, SourceFeature } from '../data/types';

export const slopeDefaultStyle: PathOptions = {
  color: '#4b5563',
  weight: 1,
  fillColor: '#94a3b8',
  fillOpacity: 0.22,
};

export const slopeHoverStyle: PathOptions = {
  color: '#111827',
  weight: 2,
  fillOpacity: 0.35,
};

export const slopeFadedStyle: PathOptions = {
  color: '#9ca3af',
  weight: 0.7,
  fillColor: '#cbd5e1',
  fillOpacity: 0.08,
};

export const slopeSelectedStyle: PathOptions = {
  color: '#facc15',
  weight: 4,
  fillColor: '#fef3c7',
  fillOpacity: 0.08,
};

export function sourceStyle(feature?: SourceFeature): PathOptions {
  return {
    color: '#9a3412',
    weight: 2,
    fillColor: feature ? '#f97316' : '#fb923c',
    fillOpacity: 0.42,
    dashArray: undefined,
  };
}

export const sourceFadedStyle: PathOptions = {
  color: '#b45309',
  weight: 1,
  fillColor: '#fed7aa',
  fillOpacity: 0.12,
};

export function envelopeStyle(_feature?: EnvelopeFeature): PathOptions {
  return {
    color: '#7c3aed',
    weight: 2,
    fillColor: '#8b5cf6',
    fillOpacity: 0.24,
  };
}

export function buildingStyle(feature?: BuildingFeature): PathOptions {
  const expType = String(feature?.properties.EXP_TYPE ?? '').toUpperCase();
  if (expType === 'SOURCE') {
    return { color: '#991b1b', weight: 1.5, fillColor: '#ef4444', fillOpacity: 0.75 };
  }
  if (expType === 'RUNOUT') {
    return { color: '#075985', weight: 1.5, fillColor: '#0ea5e9', fillOpacity: 0.75 };
  }
  return { color: '#581c87', weight: 1.7, fillColor: '#d946ef', fillOpacity: 0.78 };
}

export const animatedPathStyle: PathOptions = {
  color: '#00f5d4',
  weight: 4,
  opacity: 0.95,
  lineCap: 'round',
  lineJoin: 'round',
};

export const staticPathStyle: PathOptions = {
  color: '#0891b2',
  weight: 2,
  opacity: 0.45,
};

export const ridgeStyle: PathOptions = {
  color: '#374151',
  weight: 1.3,
  opacity: 0.6,
  dashArray: '5 4',
};
