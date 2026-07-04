import React, { useState } from 'react';
import { ChartTooltip, useChartTooltip } from './ChartTooltip';
import { ORIGIN_COLORS } from './originStatsData';

// CPVs by language, split by origin (benchmark_origins.csv) and by
// challenge mode (cpv_merged.csv; delta = bug-introducing commit given,
// full = whole-project fault localization).
const TOTAL = 315;
const TABS = [
  {
    id: 'origin',
    label: 'Origin',
    keys: ['1-day', 'synthetic'],
    colors: ORIGIN_COLORS,
    rows: [
      { language: 'C/C++', '1-day': 19, synthetic: 104 },
      { language: 'JVM', '1-day': 101, synthetic: 91 },
    ],
  },
  {
    id: 'mode',
    label: 'Mode',
    keys: ['delta', 'full'],
    colors: { delta: '#76B7B2', full: '#B07AA1' },
    rows: [
      { language: 'C/C++', delta: 77, full: 46 },
      { language: 'JVM', delta: 137, full: 55 },
    ],
  },
];

/** Interactive bars of CPVs per language, split by origin or mode. */
export default function OriginBars() {
  const [tabId, setTabId] = useState('origin');
  const { containerRef, tip, showTip, hideTip } = useChartTooltip();
  const tab = TABS.find((t) => t.id === tabId);
  const max = Math.max(...tab.rows.flatMap((r) => tab.keys.map((k) => r[k])));

  return (
    <div className="origin-bars chart-tooltip__container" ref={containerRef}>
      <h4 className="donut__title">CPVs by language</h4>
      <div className="results-tabs results-tabs--center" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tabId === t.id}
            className={`results-tab ${tabId === t.id ? 'results-tab--active' : ''}`}
            onClick={() => setTabId(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="origin-bars__legend">
        {tab.keys.map((k) => (
          <span key={k} className="overlap__legend-item">
            <span className="overlap__swatch" style={{ background: tab.colors[k] }} />
            {k}
          </span>
        ))}
      </div>
      <div className="origin-vbars">
        {tab.rows.map((row) => (
          <div key={row.language} className="origin-vbars__group">
            <div className="origin-vbars__bars">
              {tab.keys.map((k) => (
                <div key={k} className="origin-vbars__col">
                  <span className="origin-vbars__count">{row[k]}</span>
                  <span
                    className="origin-vbars__bar"
                    style={{
                      height: `${(row[k] / max) * 100}%`,
                      background: tab.colors[k],
                    }}
                    onMouseMove={(e) =>
                      showTip(
                        e,
                        <>
                          <strong>
                            {row.language} · {k}
                          </strong>
                          <br />
                          {row[k]} CPVs ({((row[k] / TOTAL) * 100).toFixed(1)}% of {TOTAL})
                        </>
                      )
                    }
                    onMouseLeave={hideTip}
                  />
                </div>
              ))}
            </div>
            <span className="origin-vbars__language">{row.language}</span>
          </div>
        ))}
      </div>
      <ChartTooltip tip={tip} />
    </div>
  );
}
