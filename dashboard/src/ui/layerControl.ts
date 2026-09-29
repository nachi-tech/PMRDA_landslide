export interface ToggleHandlers {
  onSources: (visible: boolean) => void;
  onEnvelopes: (visible: boolean) => void;
  onBuildings: (visible: boolean) => void;
  onRidges: (visible: boolean) => void;
}

function bindToggle(id: string, handler: (visible: boolean) => void): void {
  const input = document.getElementById(id) as HTMLInputElement | null;
  input?.addEventListener('change', () => handler(input.checked));
}

export function bindLayerControls(handlers: ToggleHandlers): void {
  bindToggle('toggle-sources', handlers.onSources);
  bindToggle('toggle-envelopes', handlers.onEnvelopes);
  bindToggle('toggle-buildings', handlers.onBuildings);
  bindToggle('toggle-ridges', handlers.onRidges);
}
