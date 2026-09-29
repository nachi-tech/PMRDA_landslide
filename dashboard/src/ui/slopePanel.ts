import type { BuildingFeature, EnvelopeFeature, MitigationIndexEntry, MitigationSlopeResult, RunoutPathFeature, SlopeFeature, SourceFeature } from '../data/types';
import { idKey } from '../data/indexes';
import { susceptibilityComposition } from '../charts/susceptibilityComposition';

function fmt(value: unknown, suffix = '', digits = 1): string {
  if (typeof value === 'number' && Number.isFinite(value)) return `${value.toFixed(digits)}${suffix}`;
  if (value == null || value === '') return '—';
  return String(value);
}

function pct(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return `${Math.round(value)}%`;
}

function htmlEscape(value: unknown): string {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function table(rows: Array<[string, unknown, string?]>): string {
  return `<dl>${rows.map(([label, value, suffix]) => `<div><dt>${htmlEscape(label)}</dt><dd>${fmt(value, suffix)}</dd></div>`).join('')}</dl>`;
}

function exposureCounts(buildings: BuildingFeature[] | null): Record<string, number> | null {
  if (buildings == null) return null;
  return buildings.reduce<Record<string, number>>((acc, building) => {
    const type = String(building.properties.EXP_TYPE ?? 'UNKNOWN').toUpperCase();
    acc[type] = (acc[type] ?? 0) + 1;
    return acc;
  }, {});
}

function renderTabButton(id: string, label: string, active = false): string {
  return `<button type="button" class="panel-tab ${active ? 'active' : ''}" data-panel-tab="${id}" aria-selected="${active}">${label}</button>`;
}

function renderMeter(value: number, className = ''): string {
  const bounded = Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));
  return `<div class="metric-meter ${className}" aria-label="${Math.round(bounded)} percent"><span style="width:${bounded}%"></span></div>`;
}

function renderList(items: string[] | undefined, empty: string): string {
  const filtered = (items ?? []).filter(Boolean);
  if (!filtered.length) return `<p class="muted">${htmlEscape(empty)}</p>`;
  return `<ul>${filtered.map((item) => `<li>${htmlEscape(item)}</li>`).join('')}</ul>`;
}

function renderMitigation(
  mitigation: MitigationSlopeResult | null,
  mitigationIndex: MitigationIndexEntry | null,
  mitigationStatus: 'idle' | 'loading' | 'loaded' | 'error',
  mitigationError: string | null,
): string {
  const renderIndexOnly = (message: string): string => {
    const scores = mitigationIndex?.policy_scores ?? {};
    const labels: Record<string, string> = {
      jute: 'Jute + vegetation',
      shotcrete: 'Shotcrete',
      wall: 'Retaining wall',
      drainage: 'Drainage',
      geometry: 'Geometry modification',
    };
    const scoreCards = Object.entries(scores).map(([policy, score]) => `
      <article class="mitigation-score ${policy === mitigationIndex?.top_policy ? 'top' : ''}">
        <div><strong>${htmlEscape(labels[policy] ?? policy)}</strong><span class="rank-pill">Index</span></div>
        ${renderMeter(score)}
        <div class="score-meta"><span>Suitability ${pct(score)}</span><span>${policy === mitigationIndex?.top_policy ? `Top option, confidence ${pct(mitigationIndex.top_confidence)}` : 'Index score'}</span></div>
      </article>
    `).join('');
    return `
      <div class="mitigation-disclaimer">
        Screening decision-support only. Suitability percentages are a match to transparent criteria, not engineering design, factor of safety, probability of success, cost estimate or construction recommendation.
      </div>
      ${mitigationIndex ? `<div class="priority-strip ${String(mitigationIndex.priority_class ?? '').toLowerCase()}"><strong>Mitigation priority:</strong> ${pct(mitigationIndex.priority_score)} (${htmlEscape(mitigationIndex.priority_class ?? '—')})<span>Top index option: ${htmlEscape(mitigationIndex.top_label ?? mitigationIndex.top_policy ?? '—')}</span></div>` : ''}
      ${scoreCards ? `<h4>Intervention suitability index</h4><div class="mitigation-grid">${scoreCards}</div>` : ''}
      <p class="muted">${htmlEscape(message)}</p>
    `;
  };

  if (mitigationStatus === 'loading') {
    return renderIndexOnly('Loading per-slope explanation…');
  }

  if (mitigationStatus === 'error') {
    if (mitigationIndex) {
      return renderIndexOnly(`Detailed why/why-not factors are unavailable for this slope: ${mitigationError ?? 'unknown detail loading error'}`);
    }
    return `<p class="warning">Mitigation detail failed to load for this slope.</p><p class="muted">${htmlEscape(mitigationError ?? 'Unknown mitigation detail loading error.')}</p>`;
  }

  if (!mitigation?.policies?.length) {
    return `
      <p class="warning">Mitigation suitability output is not available for this slope.</p>
      <p class="muted">Run <code>python pmrda_mitigation_suitability.py</code> from the repository root after preparing dashboard data. The dashboard expects <code>mitigation_index.json</code> and per-slope files under <code>mitigation/by_su/</code>.</p>
    `;
  }

  const policies = [...mitigation.policies].sort((a, b) => a.RANK - b.RANK);
  const top = policies[0];
  const closeNames = policies
    .filter((policy) => policy.IS_LEADING_CANDIDATE)
    .map((policy) => policy.SHORT_LABEL || policy.POLICY_LABEL);
  const complementary = top.COMPLEMENTARY_MEASURES ?? [];

  const scoreCards = policies.map((policy) => `
    <article class="mitigation-score ${policy.RANK === 1 ? 'top' : ''}">
      <div>
        <strong>${htmlEscape(policy.SHORT_LABEL || policy.POLICY_LABEL)}</strong>
        <span class="rank-pill">Rank ${policy.RANK}</span>
      </div>
      ${renderMeter(policy.SCORE)}
      <div class="score-meta"><span>Suitability ${pct(policy.SCORE)}</span><span>Confidence ${pct(policy.CONFIDENCE)}</span></div>
    </article>
  `).join('');

  const factorRows = top.components.map((component) => `
    <tr>
      <td>${htmlEscape(component.FACTOR_LABEL)}</td>
      <td>${fmt(component.RAW_VALUE)}</td>
      <td>${fmt(component.FACTOR_SCORE, '%', 0)}</td>
      <td>${htmlEscape(component.STATUS)}</td>
    </tr>
  `).join('');

  return `
    <div class="mitigation-disclaimer">
      Screening decision-support only. Suitability percentages are a match to transparent criteria, not engineering design, factor of safety, probability of success, cost estimate or construction recommendation.
    </div>
    <div class="priority-strip ${String(top.PRIORITY_CLASS).toLowerCase()}">
      <strong>Mitigation priority:</strong> ${pct(top.PRIORITY_SCORE)} (${htmlEscape(top.PRIORITY_CLASS)})
      <span>Priority explains urgency/importance; suitability explains treatment fit.</span>
    </div>
    <h4>Intervention suitability</h4>
    <div class="mitigation-grid">${scoreCards}</div>
    <h4>Top screening option</h4>
    ${table([
      ['Leading option', top.POLICY_LABEL],
      ['Suitability', top.SCORE, '%'],
      ['Data confidence', top.CONFIDENCE, '%'],
      ['Sensitivity', top.SENSITIVITY_CLASS],
    ])}
    <p class="muted">${htmlEscape(top.RANK_NOTE ?? '')} ${htmlEscape(top.SENSITIVITY_NOTE ?? '')}</p>
    <h4>Why this option?</h4>
    ${renderList(top.REASONS, 'No strong positive factors available under current Level A data.')}
    <h4>Why not / data gaps</h4>
    ${renderList(top.LIMITATIONS, 'No major limitation identified in available Level A data.')}
    <h4>Close alternatives</h4>
    ${renderList(closeNames, 'No close alternatives under the configured score-delta threshold.')}
    <h4>Complementary treatments</h4>
    ${renderList(complementary, 'No configured complementary treatment combination triggered.')}
    <details>
      <summary>Technical suitability factors for top option</summary>
      <table><thead><tr><th>Factor</th><th>Value</th><th>Score</th><th>Status</th></tr></thead><tbody>${factorRows}</tbody></table>
    </details>
  `;
}

export interface PanelInput {
  slope: SlopeFeature | null;
  sources: SourceFeature[];
  paths: RunoutPathFeature[];
  envelopes: EnvelopeFeature[];
  buildings: BuildingFeature[] | null;
  mitigation: MitigationSlopeResult | null;
  mitigationIndex: MitigationIndexEntry | null;
  mitigationStatus: 'idle' | 'loading' | 'loaded' | 'error';
  mitigationError: string | null;
  selectedSourceId: string | null;
  onSourceSelect: (sourceId: string | null) => void;
}

export function renderEmptyPanel(container: HTMLElement): void {
  container.innerHTML = `
    <section class="panel-card">
      <h2>No slope selected</h2>
      <p>Click a slope unit on the map or search by <code>SU_ID</code> to inspect candidate source zones, stored runout paths, envelopes, exposed buildings and mitigation screening suitability.</p>
    </section>
  `;
}

export function renderSlopePanel(container: HTMLElement, input: PanelInput): void {
  if (!input.slope) {
    renderEmptyPanel(container);
    return;
  }

  const p = input.slope.properties;
  const sourceOptions = input.sources.length
    ? `<div class="source-picker"><button type="button" data-source="" class="${input.selectedSourceId ? '' : 'active'}">All sources</button>${input.sources.map((source) => {
        const id = idKey(source.properties.SOURCE_ID);
        return `<button type="button" data-source="${id}" class="${input.selectedSourceId === id ? 'active' : ''}">Source ${id}</button>`;
      }).join('')}</div>`
    : '<p class="warning">No candidate source zone is associated with this slope under the active screening scenario.</p>';

  const sourceList = input.sources.map((source) => `
    <li>
      <strong>SOURCE_ID ${source.properties.SOURCE_ID}</strong>
      ${table([
        ['Area', source.properties.AREA_HA, ' ha'],
        ['Mean slope', source.properties.S_MEAN, '°'],
        ['P90 slope', source.properties.S_P90, '°'],
        ['Mean LSI', source.properties.LSI_MEAN],
        ['Relative position', source.properties.REL_POS],
      ])}
    </li>`).join('');

  const runoutSummary = input.paths.length
    ? table([
        ['Runout path count', input.paths.length],
        ['Envelope count', input.envelopes.length],
        ['Max path length', Math.max(...input.paths.map((path) => Number(path.properties.LENGTH_M ?? 0))), ' m'],
        ['Max drop', Math.max(...input.paths.map((path) => Number(path.properties.DROP_M ?? 0))), ' m'],
      ])
    : input.sources.length
      ? '<p class="warning">Candidate source exists, but no runout path is available in the current output.</p>'
      : '<p class="muted">Animation disabled until source/runout data are available.</p>';

  const exposure = exposureCounts(input.buildings);
  const exposureHtml = exposure == null
    ? '<p class="warning">Exposure data unavailable.</p>'
    : `<p><strong>${input.buildings?.length ?? 0}</strong> exposed building relationship(s).</p>${table([
        ['SOURCE', exposure.SOURCE ?? 0],
        ['RUNOUT', exposure.RUNOUT ?? 0],
        ['BOTH', exposure.BOTH ?? 0],
        ['Total overlap', input.buildings?.reduce((sum, building) => sum + Number(building.properties.TOT_OVLP ?? 0), 0) ?? 0, ' m²'],
      ])}<p class="muted">Building exposure means footprint intersection with modelled source/runout screening geometry, not damage or risk.</p>`;

  container.innerHTML = `
    <section class="panel-card">
      <h2>Selected slope: SU_ID ${p.SU_ID}</h2>
      <div class="panel-tabs" role="tablist">
        ${renderTabButton('overview', 'Overview', true)}
        ${renderTabButton('mitigation', 'Mitigation')}
      </div>
      <div class="panel-tab-content active" data-panel-tab-content="overview">
        <h3>A. Slope</h3>
        ${table([
          ['Area', p.AREA_HA, ' ha'], ['Mean slope', p.S_MEAN, '°'], ['P90 slope', p.S_P90, '°'], ['Maximum slope', p.S_MAX, '°'],
          ['Aspect', p.ASP_MEAN, '°'], ['Aspect concentration', p.ASP_CONC], ['Relief', p.RELIEF, ' m'],
        ])}
        <h3>B. Susceptibility</h3>
        ${table([
          ['Mean LSI', p.MEAN_LSI], ['High / very high', p.HIGH_PCT, '%'], ['Dominant class', p.DOM_CLASS],
        ])}
        ${susceptibilityComposition(p)}
        <h3>C. Candidate release zones</h3>
        ${sourceOptions}
        <ul class="feature-list">${sourceList}</ul>
        <h3>D. Runout</h3>
        ${runoutSummary}
        ${input.envelopes.length || input.paths.length === 0 ? '' : '<p class="warning">Runout envelope unavailable.</p>'}
        <p class="muted">Animation reveal is an ordering/communication aid and is not physical travel time.</p>
        <h3>E. Exposure</h3>
        ${exposureHtml}
      </div>
      <div class="panel-tab-content" data-panel-tab-content="mitigation">
        <h3>F. Mitigation suitability</h3>
        ${renderMitigation(input.mitigation, input.mitigationIndex, input.mitigationStatus, input.mitigationError)}
      </div>
    </section>
  `;

  container.querySelectorAll<HTMLButtonElement>('[data-source]').forEach((button) => {
    button.addEventListener('click', () => input.onSourceSelect(button.dataset.source || null));
  });

  container.querySelectorAll<HTMLButtonElement>('[data-panel-tab]').forEach((button) => {
    button.addEventListener('click', () => {
      const tabId = button.dataset.panelTab;
      container.querySelectorAll<HTMLButtonElement>('[data-panel-tab]').forEach((tab) => {
        const active = tab.dataset.panelTab === tabId;
        tab.classList.toggle('active', active);
        tab.setAttribute('aria-selected', String(active));
      });
      container.querySelectorAll<HTMLElement>('[data-panel-tab-content]').forEach((content) => {
        content.classList.toggle('active', content.dataset.panelTabContent === tabId);
      });
    });
  });
}
