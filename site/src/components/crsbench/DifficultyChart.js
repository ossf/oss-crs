import React, { useState } from 'react';
import { ChartTooltip, useChartTooltip } from './ChartTooltip';
import ModelIcon from './ModelIcon';
import {
  DIFFICULTY_AGENTS,
  DIFFICULTY_DATA,
  DIFFICULTY_OUTCOMES,
  DIFFICULTY_PANELS,
} from './resultsData';

function binTotal(counts) {
  return DIFFICULTY_OUTCOMES.reduce((sum, o) => sum + counts[o.key], 0);
}

/**
 * Bug-fixing outcomes bucketed by ground-truth patch-difficulty proxies:
 * stacked outcome bars per difficulty bin, filterable by agent.
 */
export default function DifficultyChart() {
  const [agent, setAgent] = useState('all');
  const { containerRef, tip, showTip, hideTip } = useChartTooltip();
  const data = DIFFICULTY_DATA[agent];

  return (
    <div className="difficulty chart-tooltip__container" ref={containerRef}>
      <div className="results-tabs" role="tablist">
        {DIFFICULTY_AGENTS.map((a) => (
          <button
            key={a.key}
            type="button"
            role="tab"
            aria-selected={agent === a.key}
            className={`results-tab ${agent === a.key ? 'results-tab--active' : ''}`}
            onClick={() => setAgent(a.key)}
          >
            <ModelIcon model={a.label} />
            {a.label}
          </button>
        ))}
      </div>

      <div className="difficulty__legend">
        {DIFFICULTY_OUTCOMES.map((o) => (
          <span key={o.key} className="overlap__legend-item">
            <span className="overlap__swatch" style={{ background: o.color }} />
            {o.label}
          </span>
        ))}
      </div>

      <div className="difficulty__grid">
        {DIFFICULTY_PANELS.map((panel) => (
          <div key={panel.key} className="difficulty__panel">
            <h4 className="difficulty__title">{panel.title}</h4>
            {panel.bins.map((bin) => {
              const counts = data[panel.key][bin];
              const total = binTotal(counts);
              const successPct = (counts.success / total) * 100;
              return (
                <div key={bin} className="difficulty__row">
                  <span className="difficulty__bin">{bin}</span>
                  <div className="difficulty__bar">
                    {DIFFICULTY_OUTCOMES.map((o) => {
                      const pct = (counts[o.key] / total) * 100;
                      if (pct === 0) return null;
                      return (
                        <span
                          key={o.key}
                          className="difficulty__segment"
                          style={{ width: `${pct}%`, background: o.color }}
                          onMouseMove={(e) =>
                            showTip(
                              e,
                              <>
                                <strong>
                                  {panel.title}: {bin}
                                </strong>
                                <br />
                                {o.label}: {counts[o.key]} of {total} trials (
                                {pct.toFixed(1)}%)
                              </>
                            )
                          }
                          onMouseLeave={hideTip}
                        >
                          {pct >= 8 ? `${pct.toFixed(0)}%` : ''}
                        </span>
                      );
                    })}
                  </div>
                  <span className="difficulty__success">
                    {successPct.toFixed(1)}%
                  </span>
                </div>
              );
            })}
          </div>
        ))}
      </div>

      <ChartTooltip tip={tip} />
    </div>
  );
}
