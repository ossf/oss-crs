import React from 'react';
import { ChartTooltip, useChartTooltip } from './ChartTooltip';
import { LOG_TICKS, ORIGIN_COLORS, ORIGIN_METRICS } from './originStatsData';

const PANEL_W = 210;
const PANEL_H = 300;
const M = { l: 40, r: 8, t: 30, b: 34 };
const PLOT_H = PANEL_H - M.t - M.b;
const COL_X = [78, 152]; // x of the 1-day / synthetic columns inside a panel
const VIOLIN_W = 30;
const RAIN_W = 16;

const ORIGINS = ['1-day', 'synthetic'];

// Group the five metrics by pipeline stage; titles are shortened because
// the group box already says finding vs fixing.
const GROUPS = [
  {
    title: 'Bug finding',
    metrics: [
      { key: 'depth', title: 'Stack depth' },
      { key: 'sfiles', title: 'Stack files' },
    ],
  },
  {
    title: 'Bug fixing',
    metrics: [
      { key: 'pfiles', title: 'Files changed' },
      { key: 'hunks', title: 'Patch hunks' },
      { key: 'lines', title: 'Lines changed' },
    ],
  },
];

// Deterministic jitter so SSR and client render identically.
function jitter(i) {
  const x = Math.sin((i + 1) * 127.1) * 43758.5453;
  return x - Math.floor(x);
}

function RaincloudPanel({ metric, title, index, showTip }) {
  const x0 = index * PANEL_W;
  const logMax = Math.log1p(metric.dmax) * 1.02;
  const yPos = (logv) => M.t + PLOT_H - (logv / logMax) * PLOT_H;
  const ticks = LOG_TICKS.filter((t) => Math.log1p(t) <= logMax);
  return (
    <g>
      <text
        x={x0 + M.l + (PANEL_W - M.l - M.r) / 2}
        y={16}
        textAnchor="middle"
        className="raincloud__panel-title"
      >
        {title}
      </text>
      {ticks.map((t) => (
        <g key={t}>
          <line
            x1={x0 + M.l}
            y1={yPos(Math.log1p(t))}
            x2={x0 + PANEL_W - M.r}
            y2={yPos(Math.log1p(t))}
            className="scatter__grid"
          />
          <text
            x={x0 + M.l - 5}
            y={yPos(Math.log1p(t))}
            textAnchor="end"
            dominantBaseline="central"
            className="scatter__tick"
          >
            {t}
          </text>
        </g>
      ))}
      {ORIGINS.map((o, oi) => {
        const d = metric.origins[o];
        const baseX = x0 + COL_X[oi];
        const violin =
          `M ${baseX} ${yPos(d.dens[0][0])} ` +
          d.dens.map(([g, w]) => `L ${baseX + w * VIOLIN_W} ${yPos(g)}`).join(' ') +
          ` L ${baseX} ${yPos(d.dens[d.dens.length - 1][0])} Z`;
        return (
          <g key={o}>
            <path
              d={violin}
              fill={ORIGIN_COLORS[o]}
              className="raincloud__violin"
              onMouseMove={(e) =>
                showTip(
                  e,
                  <>
                    <strong>
                      {title} · {o}
                    </strong>
                    <br />
                    median {d.median} across {d.n} CPVs
                  </>
                )
              }
            />
            {d.values.map((v, vi) => (
              <circle
                key={vi}
                cx={baseX - 5 - jitter(vi + oi * 997 + index * 31) * RAIN_W}
                cy={yPos(Math.log1p(v))}
                r={2}
                fill={ORIGIN_COLORS[o]}
                className="raincloud__point"
                onMouseMove={(e) =>
                  showTip(
                    e,
                    <>
                      <strong>{o} CPV</strong>, {title.toLowerCase()}: {v}
                    </>
                  )
                }
              />
            ))}
            <line
              x1={baseX + 2}
              y1={yPos(Math.log1p(d.median))}
              x2={baseX + VIOLIN_W + 4}
              y2={yPos(Math.log1p(d.median))}
              className="raincloud__median"
            />
            <text
              x={baseX + VIOLIN_W + 7}
              y={yPos(Math.log1p(d.median))}
              dominantBaseline="central"
              className="raincloud__median-label"
            >
              {d.median}
            </text>
            <text x={baseX} y={PANEL_H - 12} textAnchor="middle" className="scatter__tick">
              {o}
            </text>
          </g>
        );
      })}
    </g>
  );
}

/**
 * Raincloud comparison of 1-day vs synthetic CPV complexity: per-CPV
 * points ("rain"), a half-violin density ("cloud"), and the median bar,
 * on a log(1+x) axis, an interactive port of the paper's raincloud,
 * split into bug-finding and bug-fixing metric groups.
 */
export default function OriginRaincloud() {
  const { containerRef, tip, showTip, hideTip } = useChartTooltip();
  const byKey = Object.fromEntries(ORIGIN_METRICS.map((m) => [m.key, m]));

  return (
    <div className="raincloud chart-tooltip__container" ref={containerRef}>
      <div className="origin-bars__legend">
        {ORIGINS.map((o) => (
          <span key={o} className="overlap__legend-item">
            <span className="overlap__swatch" style={{ background: ORIGIN_COLORS[o] }} />
            {o}
          </span>
        ))}
      </div>
      <div className="raincloud__groups">
        {GROUPS.map((group) => (
          <div key={group.title} className="raincloud__group">
            <h4 className="donut__title">{group.title}</h4>
            <div className="raincloud__scroll">
              <svg
                viewBox={`0 0 ${PANEL_W * group.metrics.length} ${PANEL_H}`}
                width={PANEL_W * group.metrics.length}
                height={PANEL_H}
                className="raincloud__svg"
                role="img"
                aria-label={`Raincloud plots of ${group.title.toLowerCase()} complexity for 1-day and synthetic vulnerabilities`}
                onMouseLeave={hideTip}
              >
                {group.metrics.map((m, i) => (
                  <RaincloudPanel
                    key={m.key}
                    metric={byKey[m.key]}
                    title={m.title}
                    index={i}
                    showTip={showTip}
                  />
                ))}
              </svg>
            </div>
          </div>
        ))}
      </div>
      <ChartTooltip tip={tip} />
    </div>
  );
}
