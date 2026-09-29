import type { ModelMetadata } from '../data/types';

export function renderDisclaimer(container: HTMLElement, metadata: ModelMetadata): void {
  const warnings = metadata.warnings?.length ? metadata.warnings : [
    'Regional screening output, not engineering-scale design or a physical time simulation.',
    'Susceptibility is not failure probability; exposure is not damage or risk.',
  ];

  container.innerHTML = `
    <h2>Model assumptions and uncertainty</h2>
    <p>${metadata.source ?? 'Existing PMRDA analytical outputs are visualized without browser-side model recomputation.'}</p>
    <ul>${warnings.map((warning) => `<li>${warning}</li>`).join('')}</ul>
    <p class="crs-note">Analysis CRS: ${metadata.analysis_crs ?? 'EPSG:32643'}; display CRS: ${metadata.display_crs ?? 'EPSG:4326'}.</p>
  `;
}
