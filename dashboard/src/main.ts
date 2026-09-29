import './style.css';
import L from 'leaflet';
import type { LatLngBoundsExpression } from 'leaflet';
import { createMap } from './map/createMap';
import { createLayerManager, setLayerVisible } from './map/layerManager';
import { createSlopeLayer, restyleSlopes } from './map/slopeLayer';
import { drawSources } from './map/sourceLayer';
import { drawEnvelopes, drawStaticPaths } from './map/runoutLayer';
import { drawBuildings } from './map/buildingLayer';
import { loadDashboardData, loadMitigationDetail } from './data/loader';
import { buildIndexes, idKey } from './data/indexes';
import type { BuildingFeature, DashboardState, EnvelopeFeature, MitigationSlopeResult, RunoutPathFeature, SlopeFeature, SourceFeature } from './data/types';
import { renderLegend } from './ui/legend';
import { renderDisclaimer } from './ui/disclaimer';
import { renderEmptyPanel, renderSlopePanel } from './ui/slopePanel';
import { bindLayerControls } from './ui/layerControl';
import { AnimationController } from './animation/animationController';
import { animateRunoutPaths } from './animation/runoutAnimator';

const state: DashboardState = {
  selectedSuId: null,
  selectedSourceId: null,
  selectedScenario: 'default',
  animationStatus: 'idle',
  animationSpeed: 1,
  technicalMode: false,
  visibleLayers: {
    slopes: true,
    sources: true,
    paths: true,
    envelopes: true,
    buildings: true,
    ridges: true,
  },
};

const map = createMap('map');
const statusEl = document.getElementById('map-status') as HTMLElement;
const panelEl = document.getElementById('panel-content') as HTMLElement;
const legendEl = document.getElementById('legend') as HTMLElement;
const disclaimerEl = document.getElementById('disclaimer') as HTMLElement;
const animateButton = document.getElementById('animate-button') as HTMLButtonElement;
const pauseButton = document.getElementById('pause-button') as HTMLButtonElement;
const resetButton = document.getElementById('reset-button') as HTMLButtonElement;
const searchInput = document.getElementById('su-search') as HTMLInputElement;
const searchButton = document.getElementById('su-search-button') as HTMLButtonElement;

renderLegend(legendEl);
renderEmptyPanel(panelEl);

function setStatus(message: string): void {
  statusEl.textContent = message;
}

function updateButtons(pathCount: number): void {
  animateButton.disabled = !state.selectedSuId || pathCount === 0;
  pauseButton.disabled = state.animationStatus !== 'playing' && state.animationStatus !== 'paused';
  pauseButton.textContent = state.animationStatus === 'paused' ? 'Resume' : 'Pause';
}

function featuresForSelection<T extends { properties: { SOURCE_ID?: string | number | null } }>(all: T[], sourceId: string | null): T[] {
  if (!sourceId) return all;
  return all.filter((feature) => idKey(feature.properties.SOURCE_ID) === sourceId);
}

function fitToSelection(slope: SlopeFeature, sources: SourceFeature[], paths: RunoutPathFeature[], envelopes: EnvelopeFeature[]): void {
  const group = L.featureGroup();
  L.geoJSON(slope).addTo(group);
  sources.forEach((feature) => L.geoJSON(feature).addTo(group));
  paths.forEach((feature) => L.geoJSON(feature).addTo(group));
  envelopes.forEach((feature) => L.geoJSON(feature).addTo(group));
  const bounds = group.getBounds();
  if (bounds.isValid()) map.fitBounds(bounds as LatLngBoundsExpression, { padding: [24, 24], maxZoom: 16 });
}

loadDashboardData()
  .then((data) => {
    const indexes = buildIndexes(data);
    const layers = createLayerManager(map, data);
    const animationController = new AnimationController();
    const mitigationCache = new Map<string, MitigationSlopeResult | null>();
    let mitigationRequestToken = 0;
    let mitigationLoadingSuId: string | null = null;
    let mitigationError: string | null = null;
    let mitigationErrorSuId: string | null = null;

    renderDisclaimer(disclaimerEl, data.metadata);

    const slopeLayer = createSlopeLayer(data.slopes.features, (suId) => selectSlope(suId));
    map.removeLayer(layers.slopeLayer);
    slopeLayer.addTo(map);
    layers.slopeLayer = slopeLayer;

    const initialBounds = slopeLayer.getBounds();
    if (initialBounds.isValid()) map.fitBounds(initialBounds, { padding: [16, 16] });
    setStatus(`Loaded ${data.slopes.features.length.toLocaleString()} slope units. Click a slope to inspect details.`);

    function currentSelection() {
      const slope = state.selectedSuId ? indexes.slopeById.get(state.selectedSuId) ?? null : null;
      const allSources = state.selectedSuId ? indexes.sourceBySuId.get(state.selectedSuId) ?? [] : [];
      const allPaths = state.selectedSuId ? indexes.pathBySuId.get(state.selectedSuId) ?? [] : [];
      const allEnvelopes = state.selectedSuId ? indexes.envelopeBySuId.get(state.selectedSuId) ?? [] : [];
      const buildings = state.selectedSuId ? indexes.buildingBySuId.get(state.selectedSuId) ?? [] : [];
      const mitigationIndex = state.selectedSuId ? data.mitigationIndex?.[state.selectedSuId] ?? null : null;
      const mitigation = state.selectedSuId ? mitigationCache.get(state.selectedSuId) ?? null : null;
      const mitigationStatus: 'idle' | 'loading' | 'loaded' | 'error' = state.selectedSuId && mitigationLoadingSuId === state.selectedSuId
        ? 'loading'
        : state.selectedSuId && mitigationErrorSuId === state.selectedSuId && mitigationError
          ? 'error'
          : mitigation
            ? 'loaded'
            : 'idle';
      const sources = featuresForSelection(allSources, state.selectedSourceId) as SourceFeature[];
      const paths = featuresForSelection(allPaths, state.selectedSourceId) as RunoutPathFeature[];
      const envelopes = featuresForSelection(allEnvelopes, state.selectedSourceId) as EnvelopeFeature[];
      return {
        slope,
        allSources,
        sources,
        paths,
        envelopes,
        buildings: data.buildings ? buildings : null as BuildingFeature[] | null,
        mitigation,
        mitigationIndex,
        mitigationStatus,
      };
    }

    function renderSelection(): void {
      const { slope, allSources, sources, paths, envelopes, buildings, mitigation, mitigationIndex, mitigationStatus } = currentSelection();
      layers.animationLayer.clearLayers();
      drawSources(layers.sourceLayer, allSources, state.selectedSourceId, (sourceId) => {
        state.selectedSourceId = sourceId;
        renderSelection();
      });
      drawStaticPaths(layers.staticPathLayer, paths);
      layers.envelopeLayer.clearLayers();
      layers.buildingLayer.clearLayers();
      if (state.animationStatus === 'complete') {
        drawEnvelopes(layers.envelopeLayer, envelopes);
        if (buildings) drawBuildings(layers.buildingLayer, buildings);
      }
      renderSlopePanel(panelEl, {
        slope,
        sources: allSources,
        paths,
        envelopes,
        buildings,
        mitigation,
        mitigationIndex,
        mitigationStatus,
        mitigationError: state.selectedSuId && mitigationErrorSuId === state.selectedSuId ? mitigationError : null,
        selectedSourceId: state.selectedSourceId,
        onSourceSelect(sourceId) {
          state.selectedSourceId = sourceId;
          state.animationStatus = 'idle';
          animationController.reset();
          renderSelection();
        },
      });
      updateButtons(paths.length);
      if (slope) fitToSelection(slope, sources.length ? sources : allSources, paths, envelopes);
    }

    function requestMitigationDetail(suId: string): void {
      mitigationError = null;
      mitigationErrorSuId = null;
      if (!data.mitigationIndex?.[suId]) {
        mitigationLoadingSuId = null;
        mitigationCache.set(suId, null);
        return;
      }
      if (mitigationCache.has(suId)) {
        mitigationLoadingSuId = null;
        return;
      }

      const token = ++mitigationRequestToken;
      mitigationLoadingSuId = suId;
      renderSelection();
      loadMitigationDetail(suId, data.mitigationIndex[suId].detail_path)
        .then((detail) => {
          if (token !== mitigationRequestToken || state.selectedSuId !== suId) return;
          mitigationCache.set(suId, detail);
          mitigationLoadingSuId = null;
          mitigationError = null;
          mitigationErrorSuId = null;
          renderSelection();
        })
        .catch((error: unknown) => {
          if (token !== mitigationRequestToken || state.selectedSuId !== suId) return;
          mitigationCache.set(suId, null);
          mitigationLoadingSuId = null;
          mitigationError = error instanceof Error ? error.message : String(error);
          mitigationErrorSuId = suId;
          renderSelection();
        });
    }

    function selectSlope(suId: string): void {
      animationController.reset();
      layers.clearSelectionLayers();
      state.selectedSuId = suId;
      state.selectedSourceId = null;
      state.animationStatus = 'idle';
      restyleSlopes(layers.slopeLayer, suId);
      renderSelection();
      requestMitigationDetail(suId);
      const { allSources, paths } = currentSelection();
      if (allSources.length === 0) setStatus('No candidate source zone is associated with this slope under the active screening scenario.');
      else if (paths.length === 0) setStatus('Candidate source exists, but no runout path is available in the current output.');
      else setStatus(`Selected SU_ID ${suId}. Ready to animate stored runout path geometry.`);
    }

    function resetSelection(): void {
      animationController.reset();
      layers.clearSelectionLayers();
      state.selectedSuId = null;
      state.selectedSourceId = null;
      mitigationRequestToken += 1;
      mitigationLoadingSuId = null;
      mitigationError = null;
      mitigationErrorSuId = null;
      state.animationStatus = 'idle';
      restyleSlopes(layers.slopeLayer, null);
      renderEmptyPanel(panelEl);
      updateButtons(0);
      if (initialBounds.isValid()) map.fitBounds(initialBounds, { padding: [16, 16] });
      setStatus('Reset to regional slope overview.');
    }

    animateButton.addEventListener('click', () => {
      const { paths, envelopes, buildings } = currentSelection();
      if (!paths.length) return;
      state.animationStatus = 'playing';
      layers.envelopeLayer.clearLayers();
      layers.buildingLayer.clearLayers();
      updateButtons(paths.length);
      animateRunoutPaths({
        layer: layers.animationLayer,
        controller: animationController,
        paths,
        speed: state.animationSpeed,
        onStatus: setStatus,
        onComplete: () => {
          state.animationStatus = 'complete';
          drawEnvelopes(layers.envelopeLayer, envelopes);
          if (buildings) drawBuildings(layers.buildingLayer, buildings);
          updateButtons(paths.length);
          setStatus('Runout reveal complete. Existing envelope and exposed buildings are displayed.');
        },
      });
    });

    pauseButton.addEventListener('click', () => {
      if (state.animationStatus === 'playing') {
        animationController.pause();
        state.animationStatus = 'paused';
        setStatus('Runout reveal paused.');
      } else if (state.animationStatus === 'paused') {
        animationController.resume();
        state.animationStatus = 'playing';
        setStatus('Runout reveal resumed.');
      }
      updateButtons(currentSelection().paths.length);
    });

    resetButton.addEventListener('click', resetSelection);

    searchButton.addEventListener('click', () => {
      const suId = searchInput.value.trim();
      if (!suId) return;
      if (!indexes.slopeById.has(suId)) {
        setStatus(`No slope found for SU_ID ${suId}.`);
        return;
      }
      selectSlope(suId);
    });

    searchInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') searchButton.click();
    });

    bindLayerControls({
      onSources(visible) {
        state.visibleLayers.sources = visible;
        setLayerVisible(map, layers.sourceLayer, visible);
      },
      onEnvelopes(visible) {
        state.visibleLayers.envelopes = visible;
        setLayerVisible(map, layers.envelopeLayer, visible);
      },
      onBuildings(visible) {
        state.visibleLayers.buildings = visible;
        setLayerVisible(map, layers.buildingLayer, visible);
      },
      onRidges(visible) {
        state.visibleLayers.ridges = visible;
        setLayerVisible(map, layers.ridgeLayer, visible);
      },
    });
  })
  .catch((error: unknown) => {
    const message = error instanceof Error ? error.message : String(error);
    setStatus(`Dashboard data failed to load: ${message}`);
    panelEl.innerHTML = `<section class="panel-card error"><h2>Data load failed</h2><p>${message}</p><p>Run <code>python pmrda_prepare_leaflet_dashboard.py --sample-size 50</code> from the repository root to create dashboard-ready exports.</p></section>`;
  });
