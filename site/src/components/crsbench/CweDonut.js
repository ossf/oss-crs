import React, { useState } from 'react';
import { ChartTooltip, useChartTooltip } from './ChartTooltip';

// Top CWEs from data/eval/benchmark_cwes.csv (91 unique CWEs, 693 entries
// across 315 CPVs; a CPV can map to multiple CWEs).
const TOTAL_ENTRIES = 693;
const TOP_CWES = [
  ['CWE-502', 'Deserialization of Untrusted Data', 64],
  ['CWE-94', 'Code Injection', 56],
  ['CWE-470', 'Unsafe Reflection', 53],
  ['CWE-122', 'Heap-based Buffer Overflow', 37],
  ['CWE-787', 'Out-of-bounds Write', 33],
  ['CWE-400', 'Uncontrolled Resource Consumption', 27],
  ['CWE-78', 'OS Command Injection', 24],
  ['CWE-125', 'Out-of-bounds Read', 23],
  ['CWE-20', 'Improper Input Validation', 22],
  ['CWE-918', 'Server-Side Request Forgery (SSRF)', 22],
  ['CWE-121', 'Stack-based Buffer Overflow', 16],
  ['CWE-770', 'Allocation Without Limits or Throttling', 14],
];
const OTHER = ['Other', '79 more CWEs', TOTAL_ENTRIES - TOP_CWES.reduce((s, c) => s + c[2], 0)];
const SLICES = [...TOP_CWES, OTHER];

const PALETTE = [
  '#4E79A7', '#F28E2B', '#59A14E', '#E15759', '#76B7B2', '#EDC948',
  '#B07AA1', '#FF9DA7', '#9C755F', '#86BCB6', '#D37295', '#A0CBE8',
  '#BAB0AB',
];

const SIZE = 300;
const CX = SIZE / 2;
const CY = SIZE / 2;
const R_OUT = 140;
const R_IN = 82;

function polar(r, angle) {
  return [CX + r * Math.cos(angle), CY + r * Math.sin(angle)];
}

function arcPath(a0, a1) {
  const large = a1 - a0 > Math.PI ? 1 : 0;
  const [x0, y0] = polar(R_OUT, a0);
  const [x1, y1] = polar(R_OUT, a1);
  const [x2, y2] = polar(R_IN, a1);
  const [x3, y3] = polar(R_IN, a0);
  return [
    `M ${x0} ${y0}`,
    `A ${R_OUT} ${R_OUT} 0 ${large} 1 ${x1} ${y1}`,
    `L ${x2} ${y2}`,
    `A ${R_IN} ${R_IN} 0 ${large} 0 ${x3} ${y3}`,
    'Z',
  ].join(' ');
}

/** Interactive donut of the benchmark's CWE distribution. */
export default function CweDonut() {
  const [hover, setHover] = useState(null);
  const { containerRef, tip, showTip, hideTip } = useChartTooltip();

  let angle = -Math.PI / 2;
  const arcs = SLICES.map(([id, name, count], i) => {
    const span = (count / TOTAL_ENTRIES) * Math.PI * 2;
    const arc = { id, name, count, i, a0: angle, a1: angle + span };
    angle += span;
    return arc;
  });

  const tipContent = (a) => (
    <>
      <strong>{a.id}</strong>: {a.name}
      <br />
      {a.count} entries ({((a.count / TOTAL_ENTRIES) * 100).toFixed(1)}%)
    </>
  );

  return (
    <div className="donut chart-tooltip__container" ref={containerRef}>
      <h4 className="donut__title">CWE distribution</h4>
      <div className="donut__body">
        <svg
          viewBox={`0 0 ${SIZE} ${SIZE}`}
          className="donut__svg"
          role="img"
          aria-label="Donut chart of the CWE distribution across all benchmark CPVs"
          onMouseLeave={() => {
            setHover(null);
            hideTip();
          }}
        >
          {arcs.map((a) => (
            <path
              key={a.id}
              d={arcPath(a.a0, a.a1)}
              fill={PALETTE[a.i]}
              className={
                hover != null && hover !== a.i ? 'donut__arc donut__arc--dim' : 'donut__arc'
              }
              onMouseEnter={() => setHover(a.i)}
              onMouseMove={(e) => showTip(e, tipContent(a))}
            />
          ))}
          <text x={CX} y={CY - 8} textAnchor="middle" className="donut__center-count">
            {TOTAL_ENTRIES}
          </text>
          <text x={CX} y={CY + 14} textAnchor="middle" className="donut__center-label">
            CWE entries
          </text>
          <text x={CX} y={CY + 32} textAnchor="middle" className="donut__center-label">
            91 unique · 315 CPVs
          </text>
        </svg>
        <ul className="donut__legend">
          {arcs.map((a) => (
            <li
              key={a.id}
              className={
                hover != null && hover !== a.i
                  ? 'donut__legend-item donut__legend-item--dim'
                  : 'donut__legend-item'
              }
              onMouseEnter={() => setHover(a.i)}
              onMouseMove={(e) => showTip(e, tipContent(a))}
              onMouseLeave={() => {
                setHover(null);
                hideTip();
              }}
            >
              <span className="overlap__swatch" style={{ background: PALETTE[a.i] }} />
              <span className="donut__legend-id">{a.id}</span>
              <span className="donut__legend-count">{a.count}</span>
            </li>
          ))}
        </ul>
      </div>
      <ChartTooltip tip={tip} />
    </div>
  );
}
