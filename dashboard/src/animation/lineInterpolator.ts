import type { LineString, MultiLineString, Position } from 'geojson';

export type LatLngTuple = [number, number];

function toLatLng(position: Position): LatLngTuple {
  return [position[1], position[0]];
}

function planarDistance(a: Position, b: Position): number {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  return Math.sqrt(dx * dx + dy * dy);
}

export function lineCoordinates(geometry: LineString | MultiLineString): Position[][] {
  if (geometry.type === 'LineString') return [geometry.coordinates];
  return geometry.coordinates;
}

export function interpolateLine(coordinates: Position[], progress: number): LatLngTuple[] {
  if (coordinates.length === 0) return [];
  if (coordinates.length === 1 || progress <= 0) return [toLatLng(coordinates[0])];
  if (progress >= 1) return coordinates.map(toLatLng);

  const segmentLengths: number[] = [];
  let total = 0;
  for (let i = 1; i < coordinates.length; i += 1) {
    const length = planarDistance(coordinates[i - 1], coordinates[i]);
    segmentLengths.push(length);
    total += length;
  }
  if (total === 0) return coordinates.map(toLatLng);

  const target = total * progress;
  let travelled = 0;
  const result: LatLngTuple[] = [toLatLng(coordinates[0])];

  for (let i = 1; i < coordinates.length; i += 1) {
    const segmentLength = segmentLengths[i - 1];
    if (travelled + segmentLength < target) {
      result.push(toLatLng(coordinates[i]));
      travelled += segmentLength;
      continue;
    }

    const remaining = target - travelled;
    const ratio = segmentLength === 0 ? 0 : remaining / segmentLength;
    const prev = coordinates[i - 1];
    const curr = coordinates[i];
    result.push(toLatLng([
      prev[0] + (curr[0] - prev[0]) * ratio,
      prev[1] + (curr[1] - prev[1]) * ratio,
    ]));
    break;
  }

  return result;
}
