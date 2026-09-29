export function renderLegend(container: HTMLElement): void {
  container.innerHTML = `
    <h2>Legend</h2>
    <div><span class="swatch slope"></span> Slope unit</div>
    <div><span class="swatch source"></span> Candidate source zone</div>
    <div><span class="line-swatch path"></span> Modelled runout path</div>
    <div><span class="swatch envelope"></span> Runout screening envelope</div>
    <div><span class="swatch b-source"></span> SOURCE-exposed building</div>
    <div><span class="swatch b-runout"></span> RUNOUT-exposed building</div>
    <div><span class="swatch b-both"></span> BOTH exposure building</div>
    <div><span class="line-swatch ridge"></span> Retained ridge/context line</div>
  `;
}
