import React, { useState } from 'react';
import { ChartTooltip, useChartTooltip } from './ChartTooltip';
import ModelIcon, { VENDOR_ICONS, vendorForModel } from './ModelIcon';
import { LEADERBOARD } from './resultsData';

const W = 680;
const H = 400;
const M = { l: 56, r: 26, t: 20, b: 50 };
const Y_TICKS = [0, 20, 40, 60, 80, 100];

const COLORS = {
  'Opus 4.6': '#9467bd',
  'Haiku 4.5': '#1f77b4',
  'GPT-5.4-mini': '#ff7f0e',
  'Gemini 3 Flash': '#2ca02c',
  'GLM-5.1': '#d62728',
};

// Per-stage axis range and label placement (dx/dy/anchor keyed by model),
// hand-tuned so labels don't collide.
const STAGES = [
  {
    key: 'e2e',
    label: 'End-to-End',
    xMax: 11,
    xTicks: [0, 2, 4, 6, 8, 10],
    yLabel: 'E2E success rate (%)',
    xLabel: 'E2E LLM spend per trial (USD)',
    place: {
      'Opus 4.6': { dx: -12, dy: 4, anchor: 'end' },
      'Haiku 4.5': { dx: 12, dy: 4, anchor: 'start' },
      'GPT-5.4-mini': { dx: 12, dy: 16, anchor: 'start' },
      'Gemini 3 Flash': { dx: 12, dy: -8, anchor: 'start' },
      'GLM-5.1': { dx: 12, dy: 4, anchor: 'start' },
    },
  },
  {
    key: 'find',
    label: 'Find',
    xMax: 12,
    xTicks: [0, 2, 4, 6, 8, 10, 12],
    yLabel: 'Find success rate (%)',
    xLabel: 'Find LLM spend per trial (USD)',
    place: {
      'Opus 4.6': { dx: -12, dy: 4, anchor: 'end' },
      'Haiku 4.5': { dx: 12, dy: 10, anchor: 'start' },
      'GPT-5.4-mini': { dx: 12, dy: 18, anchor: 'start' },
      'Gemini 3 Flash': { dx: 12, dy: -8, anchor: 'start' },
      'GLM-5.1': { dx: 12, dy: 4, anchor: 'start' },
    },
  },
  {
    key: 'fix',
    label: 'Fix',
    xMax: 1.4,
    xTicks: [0, 0.25, 0.5, 0.75, 1, 1.25],
    yLabel: 'Conditional fix success rate (%)',
    xLabel: 'Fix LLM spend per trial (USD)',
    place: {
      'Opus 4.6': { dx: -12, dy: 4, anchor: 'end' },
      'Haiku 4.5': { dx: 12, dy: 4, anchor: 'start' },
      'GPT-5.4-mini': { dx: 12, dy: 16, anchor: 'start' },
      'Gemini 3 Flash': { dx: 12, dy: -8, anchor: 'start' },
      'GLM-5.1': { dx: 12, dy: 4, anchor: 'start' },
    },
  },
];

/**
 * EvalPlus-style cost-vs-performance scatter with one tab per pipeline
 * stage (Find / Fix / End-to-End).
 */
export default function CostScatter() {
  const [stageKey, setStageKey] = useState('e2e');
  const [hover, setHover] = useState(null);
  const { containerRef, tip, showTip, hideTip } = useChartTooltip();
  const stage = STAGES.find((s) => s.key === stageKey);

  const xPos = (cost) => M.l + (cost / stage.xMax) * (W - M.l - M.r);
  const yPos = (rate) => H - M.b - (rate / 100) * (H - M.t - M.b);
  const fmtTick = (t) => (t === 0 ? '$0' : `$${t}`);

  const tipContent = (row) => {
    const m = row[stage.key];
    return (
      <>
        <strong>
          <ModelIcon model={row.model} />
          {row.agent} ({row.model})
        </strong>
        <br />
        {stage.label} {Math.round((m.n / m.d) * 100)}% ({m.n}/{m.d}
        {stage.key === 'fix' ? ' found' : ''}) at ${m.cost.toFixed(2)}/trial
        <br />
        Find {Math.round((row.find.n / row.find.d) * 100)}% ($
        {row.find.cost.toFixed(2)}) · Fix{' '}
        {Math.round((row.fix.n / row.fix.d) * 100)}% ($
        {row.fix.cost.toFixed(2)})
      </>
    );
  };

  return (
    <div className="scatter chart-tooltip__container" ref={containerRef}>
      <div className="results-tabs" role="tablist">
        {STAGES.map((s) => (
          <button
            key={s.key}
            type="button"
            role="tab"
            aria-selected={stageKey === s.key}
            className={`results-tab ${stageKey === s.key ? 'results-tab--active' : ''}`}
            onClick={() => {
              setStageKey(s.key);
              setHover(null);
            }}
          >
            {s.label}
          </button>
        ))}
      </div>

      {stageKey === 'fix' && (
        <div className="scatter__warning">
          <strong>Different denominators.</strong> Fix rates are conditional:
          each CRS is only evaluated on the vulnerabilities <em>it found
          first</em> (<ModelIcon model="Opus" />Opus 4.6 on 47,{' '}
          <ModelIcon model="GLM" />GLM-5.1 on 43,{' '}
          <ModelIcon model="Gemini" />Gemini 3 Flash on 31,{' '}
          <ModelIcon model="GPT" />GPT-5.4-mini on 30, and{' '}
          <ModelIcon model="Haiku" />Haiku 4.5 on 23 of the 51). A weak finder is
          graded on a smaller and possibly easier set, so these rates are not
          directly comparable across CRSs.
        </div>
      )}

      <div className="scatter__scroll">
        <svg
          className="scatter__svg"
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label={`${stage.label} success rate versus LLM cost per trial for each CRS`}
          onMouseLeave={() => {
            setHover(null);
            hideTip();
          }}
        >
          {/* gridlines + ticks */}
          {Y_TICKS.map((t) => (
            <g key={`y-${t}`}>
              <line
                x1={M.l}
                y1={yPos(t)}
                x2={W - M.r}
                y2={yPos(t)}
                className="scatter__grid"
              />
              <text x={M.l - 8} y={yPos(t)} textAnchor="end" dominantBaseline="central" className="scatter__tick">
                {t}
              </text>
            </g>
          ))}
          {stage.xTicks.map((t) => (
            <g key={`x-${t}`}>
              <line
                x1={xPos(t)}
                y1={M.t}
                x2={xPos(t)}
                y2={H - M.b}
                className="scatter__grid"
              />
              <text x={xPos(t)} y={H - M.b + 16} textAnchor="middle" className="scatter__tick">
                {fmtTick(t)}
              </text>
            </g>
          ))}
          <text x={M.l + (W - M.l - M.r) / 2} y={H - 8} textAnchor="middle" className="scatter__axis-label">
            {stage.xLabel}
          </text>
          <text
            x={14}
            y={M.t + (H - M.t - M.b) / 2}
            textAnchor="middle"
            className="scatter__axis-label"
            transform={`rotate(-90 14 ${M.t + (H - M.t - M.b) / 2})`}
          >
            {stage.yLabel}
          </text>

          {/* points */}
          {LEADERBOARD.map((row, i) => {
            const m = row[stage.key];
            const p = stage.place[row.model];
            const rate = (m.n / m.d) * 100;
            const cx = xPos(m.cost);
            const cy = yPos(rate);
            const active = hover === i;
            const label = `${row.agent} (${row.model})`;
            const icon = VENDOR_ICONS[vendorForModel(row.model)];
            const iconSize = 12;
            // Icon sits just before the label text; for end-anchored labels
            // the text start is estimated from its length.
            const iconX =
              p.anchor === 'start'
                ? cx + p.dx
                : cx + p.dx - label.length * 7.1 - iconSize - 4;
            const textX = p.anchor === 'start' ? cx + p.dx + iconSize + 4 : cx + p.dx;
            return (
              <g
                key={row.agent + row.model}
                className={hover != null && !active ? 'scatter__pt--dim' : ''}
                onMouseEnter={() => setHover(i)}
                onMouseMove={(e) => showTip(e, tipContent(row))}
              >
                <circle
                  cx={cx}
                  cy={cy}
                  r={active ? 9 : 7}
                  fill={COLORS[row.model]}
                  className="scatter__dot"
                />
                <g
                  transform={`translate(${iconX}, ${cy + p.dy - iconSize + 2}) scale(${iconSize / 24})`}
                  aria-hidden="true"
                >
                  <path
                    d={icon.d}
                    fill={icon.color === 'currentColor' ? 'var(--ifm-font-color-base)' : icon.color}
                  />
                </g>
                <text
                  x={textX}
                  y={cy + p.dy}
                  textAnchor={p.anchor}
                  className="scatter__pt-label"
                >
                  {label}
                </text>
                {/* larger invisible hover target */}
                <circle cx={cx} cy={cy} r={16} fill="transparent" />
              </g>
            );
          })}
        </svg>
      </div>
      <ChartTooltip tip={tip} />
    </div>
  );
}
