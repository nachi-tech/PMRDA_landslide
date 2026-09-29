import L from 'leaflet';
import type { RunoutPathFeature } from '../data/types';
import { animatedPathStyle } from '../map/styles';
import { AnimationController } from './animationController';
import { interpolateLine, lineCoordinates } from './lineInterpolator';

export interface RunoutAnimationOptions {
  layer: L.LayerGroup;
  controller: AnimationController;
  paths: RunoutPathFeature[];
  speed: number;
  onComplete: () => void;
  onStatus?: (status: string) => void;
}

export function animateRunoutPaths(options: RunoutAnimationOptions): void {
  const { layer, controller, paths, speed, onComplete, onStatus } = options;
  controller.start();
  layer.clearLayers();

  const polylines: Array<{ coordinates: ReturnType<typeof lineCoordinates>[number]; line: L.Polyline }> = [];
  paths.forEach((feature) => {
    lineCoordinates(feature.geometry).forEach((coordinates) => {
      const line = L.polyline([], animatedPathStyle).addTo(layer);
      polylines.push({ coordinates, line });
    });
  });

  if (polylines.length === 0) {
    onStatus?.('No runout paths are available for animation.');
    onComplete();
    return;
  }

  const durationMs = Math.max(600, 2800 / Math.max(0.25, speed));
  let startTime: number | null = null;
  let pausedAt: number | null = null;
  let totalPausedMs = 0;

  const tick = (timestamp: number) => {
    if (controller.isCancelled()) return;

    if (controller.isPaused()) {
      if (pausedAt == null) pausedAt = timestamp;
      controller.requestFrame(tick);
      return;
    }

    if (pausedAt != null) {
      totalPausedMs += timestamp - pausedAt;
      pausedAt = null;
    }

    if (startTime == null) startTime = timestamp;
    const elapsed = timestamp - startTime - totalPausedMs;
    const progress = Math.min(1, elapsed / durationMs);

    polylines.forEach(({ coordinates, line }) => {
      line.setLatLngs(interpolateLine(coordinates, progress));
    });

    onStatus?.(`Revealing stored runout trajectories: ${Math.round(progress * 100)}%`);

    if (progress < 1) {
      controller.requestFrame(tick);
    } else {
      onComplete();
    }
  };

  controller.requestFrame(tick);
}
