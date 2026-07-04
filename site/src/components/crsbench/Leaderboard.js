import React, { useMemo, useState } from 'react';
import ModelIcon from './ModelIcon';
import { LEADERBOARD } from './resultsData';

const MEDALS = ['🥇', '🥈', '🥉'];

const COLUMNS = [
  { key: 'crs', label: 'CRS', sortable: false },
  { key: 'find.rate', label: 'Find', group: 'find', kind: 'rate' },
  { key: 'find.cost', label: 'Find $/trial', group: 'find', kind: 'cost' },
  { key: 'fix.rate', label: 'Fix*', group: 'fix', kind: 'rate' },
  { key: 'fix.cost', label: 'Fix $/trial', group: 'fix', kind: 'cost' },
  { key: 'e2e.rate', label: 'End-to-End', group: 'e2e', kind: 'rate' },
  { key: 'e2e.cost', label: 'E2E $/trial', group: 'e2e', kind: 'cost' },
];

function value(row, key) {
  const [group, kind] = key.split('.');
  const m = row[group];
  return kind === 'rate' ? m.n / m.d : m.cost;
}

function RateCell({ metric, group }) {
  const pct = (metric.n / metric.d) * 100;
  return (
    <div className={`lb-bar lb-bar--${group}`}>
      <span className="lb-bar__fill" style={{ width: `${pct}%` }} />
      <span className="lb-bar__text">
        {Math.round(pct)}%{' '}
        <small>
          ({metric.n}/{metric.d})
        </small>
      </span>
    </div>
  );
}

/**
 * Sortable end-to-end leaderboard (RQ2). Medals follow end-to-end
 * success regardless of the active sort.
 */
export default function Leaderboard() {
  const [sort, setSort] = useState({ key: 'e2e.rate', dir: -1 });

  const medals = useMemo(() => {
    const order = [...LEADERBOARD].sort(
      (a, b) => b.e2e.n / b.e2e.d - a.e2e.n / a.e2e.d
    );
    const m = new Map();
    order.forEach((row, i) => m.set(row, MEDALS[i] || ''));
    return m;
  }, []);

  const rows = useMemo(() => {
    const sorted = [...LEADERBOARD].sort(
      (a, b) => (value(a, sort.key) - value(b, sort.key)) * sort.dir
    );
    return sorted;
  }, [sort]);

  const onSort = (key) => {
    setSort((prev) =>
      prev.key === key ? { key, dir: -prev.dir } : { key, dir: -1 }
    );
  };

  return (
    <div className="lb">
      <div className="lb__scroll">
        <table className="lb__table">
          <thead>
            <tr>
              {COLUMNS.map((col) =>
                col.sortable === false ? (
                  <th key={col.key}>{col.label}</th>
                ) : (
                  <th key={col.key} aria-sort={sort.key === col.key ? (sort.dir === -1 ? 'descending' : 'ascending') : 'none'}>
                    <button
                      type="button"
                      className={`lb__sort ${sort.key === col.key ? 'lb__sort--active' : ''}`}
                      onClick={() => onSort(col.key)}
                    >
                      {col.label}
                      <span className="lb__sort-arrow" aria-hidden="true">
                        {sort.key === col.key ? (sort.dir === -1 ? '▼' : '▲') : '↕'}
                      </span>
                    </button>
                  </th>
                )
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={`${row.agent}-${row.model}`}>
                <td className="lb__crs">
                  <span className="lb__medal" aria-hidden="true">
                    {medals.get(row)}
                  </span>
                  <span>
                    <ModelIcon model={row.model} />
                    <strong>{row.agent}</strong>{' '}
                    <span className="lb__model">{row.model}</span>
                  </span>
                </td>
                <td>
                  <RateCell metric={row.find} group="find" />
                </td>
                <td className="lb__cost">${row.find.cost.toFixed(2)}</td>
                <td>
                  <RateCell metric={row.fix} group="fix" />
                </td>
                <td className="lb__cost">${row.fix.cost.toFixed(2)}</td>
                <td>
                  <RateCell metric={row.e2e} group="e2e" />
                </td>
                <td className="lb__cost">${row.e2e.cost.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="lb__footnote">
        Success = vulnerabilities solved at least once across 3 trials on the
        51-vulnerability AFC subset; cost = mean per-trial LLM spend. *Fix is
        conditional on the vulnerability being found first. Click a column
        header to sort.
      </p>
    </div>
  );
}
