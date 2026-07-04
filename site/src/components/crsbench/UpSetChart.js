import React, { useState } from 'react';
import { ChartTooltip, useChartTooltip } from './ChartTooltip';

const COL_W = 42;
const ROW_H = 28;
const BAR_H = 118;
const LABEL_W = 128;
const TOTAL_W = 100;
const GAP = 14;
const TOP_PAD = 20;

function describeMembership(bits, sets) {
  const inSets = sets.filter((_, i) => bits[i] === '1').map((s) => s.label);
  if (inSets.length === 0) return 'missed by every configuration';
  if (inSets.length === sets.length) {
    return inSets.length === 2
      ? `found by both ${inSets.join(' and ')}`
      : `found by all: ${inSets.join(', ')}`;
  }
  if (inSets.length === 1) return `found only by ${inSets[0]}`;
  return `found by ${inSets.join(' + ')} only`;
}

/**
 * Interactive UpSet plot: one column per exclusive membership class,
 * with per-set totals on the left. Hovering a column explains it.
 */
export default function UpSetChart({ data }) {
  const { sets, intersections, universe, unit } = data;
  const [hover, setHover] = useState(null);
  const { containerRef, tip, showTip, hideTip } = useChartTooltip();

  const cols = intersections;
  const maxCount = Math.max(...cols.map((c) => c.count));
  const maxTotal = Math.max(...sets.map((s) => s.total));
  const matrixLeft = LABEL_W + TOTAL_W + GAP;
  const width = matrixLeft + cols.length * COL_W + 8;
  const matrixTop = TOP_PAD + BAR_H + 12;
  const height = matrixTop + sets.length * ROW_H + 8;
  const colX = (i) => matrixLeft + i * COL_W + COL_W / 2;
  const rowY = (i) => matrixTop + i * ROW_H + ROW_H / 2;

  const isNone = (bits) => !bits.includes('1');
  const dimmed = (i) => hover != null && hover !== i;

  const tipContent = (c) => (
    <>
      <strong>
        {c.count} {unit}
      </strong>{' '}
      ({((c.count / universe) * 100).toFixed(1)}% of {universe})
      <br />
      {describeMembership(c.bits, sets)}
    </>
  );

  return (
    <div className="upset chart-tooltip__container" ref={containerRef}>
      <div className="upset__scroll">
        <svg
          className="upset__svg"
          viewBox={`0 0 ${width} ${height}`}
          width={width}
          height={height}
          role="img"
          aria-label="UpSet plot of vulnerabilities found by each CRS configuration"
          onMouseLeave={() => {
            setHover(null);
            hideTip();
          }}
        >
          {/* alternating row shading behind the membership matrix */}
          {sets.map((s, i) =>
            i % 2 === 0 ? (
              <rect
                key={`shade-${s.key}`}
                x={0}
                y={matrixTop + i * ROW_H}
                width={width}
                height={ROW_H}
                className="upset__row-shade"
              />
            ) : null
          )}

          {/* per-set labels and total bars */}
          {sets.map((s, i) => (
            <g key={`set-${s.key}`}>
              <text
                x={LABEL_W - 6}
                y={rowY(i)}
                textAnchor="end"
                dominantBaseline="central"
                className="upset__set-label"
              >
                {s.label}
              </text>
              <rect
                x={LABEL_W}
                y={rowY(i) - 7}
                width={Math.max(2, (s.total / maxTotal) * (TOTAL_W - 34))}
                height={14}
                rx={2}
                className="upset__total-bar"
              />
              <text
                x={LABEL_W + (s.total / maxTotal) * (TOTAL_W - 34) + 5}
                y={rowY(i)}
                dominantBaseline="central"
                className="upset__total-count"
              >
                {s.total}
              </text>
            </g>
          ))}

          {/* intersection columns */}
          {cols.map((c, i) => {
            const barH = Math.max(2, (c.count / maxCount) * (BAR_H - 26));
            const x = colX(i);
            const onRows = sets.map((_, r) => c.bits[r] === '1');
            const firstOn = onRows.indexOf(true);
            const lastOn = onRows.lastIndexOf(true);
            return (
              <g
                key={c.bits}
                className={[
                  'upset__col',
                  isNone(c.bits) ? 'upset__col--none' : '',
                  dimmed(i) ? 'upset__col--dim' : '',
                ].join(' ')}
              >
                <rect
                  x={x - COL_W / 2 + 3}
                  y={TOP_PAD + BAR_H - barH}
                  width={COL_W - 6}
                  height={barH}
                  rx={3}
                  className="upset__bar"
                />
                <text
                  x={x}
                  y={TOP_PAD + BAR_H - barH - 5}
                  textAnchor="middle"
                  className="upset__bar-count"
                >
                  {c.count}
                </text>
                {firstOn >= 0 && firstOn !== lastOn && (
                  <line
                    x1={x}
                    y1={rowY(firstOn)}
                    x2={x}
                    y2={rowY(lastOn)}
                    className="upset__link"
                  />
                )}
                {sets.map((s, r) => (
                  <circle
                    key={`${c.bits}-${s.key}`}
                    cx={x}
                    cy={rowY(r)}
                    r={6.5}
                    className={
                      c.bits[r] === '1' ? 'upset__dot upset__dot--on' : 'upset__dot'
                    }
                  />
                ))}
                {/* transparent hover target covering the whole column */}
                <rect
                  x={x - COL_W / 2}
                  y={0}
                  width={COL_W}
                  height={height}
                  fill="transparent"
                  onMouseEnter={() => setHover(i)}
                  onMouseMove={(e) => showTip(e, tipContent(c))}
                />
              </g>
            );
          })}
        </svg>
      </div>
      <ChartTooltip tip={tip} />
    </div>
  );
}
