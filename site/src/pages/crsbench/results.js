import React, { useState } from 'react';
import Layout from '@theme/Layout';
import Link from '@docusaurus/Link';
import CostScatter from '@site/src/components/crsbench/CostScatter';
import DifficultyChart from '@site/src/components/crsbench/DifficultyChart';
import Leaderboard from '@site/src/components/crsbench/Leaderboard';
import ModelIcon from '@site/src/components/crsbench/ModelIcon';
import UpSetChart from '@site/src/components/crsbench/UpSetChart';
import {
  SETUP_STATS,
  FINDING_SUMMARY,
  FINDING_SUMMARY_HARD,
  FIXING_SUMMARY,
  UPSET_FULL,
  UPSET_HYBRID,
} from '@site/src/components/crsbench/resultsData';

function Hero() {
  return (
    <header className="hero hero--oss-crs hero--results">
      <div className="container text--center">
        <h1 className="hero__title">CRSBench Results</h1>
        <p className="hero__subtitle">
          How today's bug-finding and bug-fixing CRSs perform on CRSBench.
        </p>
        <div className="hero-stats">
          {SETUP_STATS.map((s) => (
            <div key={s.label} className="hero-stat">
              <span className="hero-stat__count">{s.count}</span>
              <span className="hero-stat__label">{s.label}</span>
            </div>
          ))}
        </div>
      </div>
    </header>
  );
}

function LeaderboardSection() {
  return (
    <section className="section section--alt">
      <div className="container">
        <h2 id="e2e-results">End-to-End Results</h2>
        <p>
          Five LLM agent-based CRSs run the <strong>full pipeline</strong>{' '}
          (find a vulnerability, then fix it) on a partial AFC subset of 51
          vulnerabilities, using identical prompts and changing only the
          vendor-provided coding-agent harness. The finding stage is the main
          differentiator: bug-finding success spans 45–92% while conditional
          fix rates cluster at 70–94%, and finding dominates the LLM cost
          ($1.09–$10.91 per trial vs. $0.12–$1.19 for fixing).
        </p>
        <CostScatter />
        <Leaderboard />
      </div>
    </section>
  );
}

const OVERLAP_TABS = [
  {
    id: 'full',
    label: 'All benchmarks (304 CPVs)',
    data: UPSET_FULL,
    summary: FINDING_SUMMARY,
    description: (
      <>
        Across all 304 CPVs, the fuzzer and the LLM agent largely{' '}
        <strong>agree on the easy bugs and diverge on the rest</strong>: 74 of
        the fuzzer's 80 CPVs (93%) are also found by the agent, but the agent
        reaches 170 CPVs the fuzzer never triggers, and the fuzzer still finds
        6 CPVs the agent misses. Neither approach subsumes the other.
      </>
    ),
  },
  {
    id: 'hybrid',
    label: 'Hard subset (95 CPVs)',
    data: UPSET_HYBRID,
    summary: FINDING_SUMMARY_HARD,
    description: (
      <>
        On the 28 hardest benchmarks (95 CPVs) where neither standalone CRS
        found everything, the fuzzer and agent together find 41 CPVs, and the
        hybrid CRS, running both concurrently with a shared corpus, finds 47:
        it re-discovers most of what the standalone components found and adds{' '}
        <strong>12 CPVs missed by both</strong>, while dropping only 6.
      </>
    ),
  },
];

function OverlapSection() {
  const [tab, setTab] = useState('full');
  const active = OVERLAP_TABS.find((t) => t.id === tab);
  return (
    <section className="section">
      <div className="container">
        <h2 id="finding-overlap">Bug-Finding Overlap Across CRSs</h2>
        <p>
          We compare bug-finding CRSs on 117 benchmarks (304 CPVs): a{' '}
          <strong>fuzzer-only</strong> baseline running each project's own
          fuzzer, an <strong>LLM agent</strong> (<ModelIcon model="Opus" />Claude Code, Opus 4.6) with
          basic bug-finding instructions, and, on the hardest subset, a{' '}
          <strong>hybrid</strong> CRS running both concurrently with a shared
          corpus. Each column below is one exclusive overlap class: which
          configurations found exactly those CPVs.
        </p>

        <div className="results-tabs" role="tablist">
          {OVERLAP_TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              className={`results-tab ${tab === t.id ? 'results-tab--active' : ''}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <p>{active.description}</p>
        <UpSetChart data={active.data} />

        <h3>Per-configuration summary</h3>
        <div className="lb__scroll">
          <table className="lb__table results-table">
            <thead>
              <tr>
                <th>CRS</th>
                <th>Solved CPVs</th>
                <th>Trial success</th>
                <th>Time to trigger</th>
                <th>LLM $/trial</th>
              </tr>
            </thead>
            <tbody>
              {active.summary.map((row) => (
                <tr key={row.crs}>
                  <td>
                    <ModelIcon model={row.id} />
                    <strong>{row.crs}</strong>{' '}
                    <span className="lb__model">{row.id}</span>
                  </td>
                  <td>
                    <div className="lb-bar lb-bar--find">
                      <span
                        className="lb-bar__fill"
                        style={{ width: `${(row.solved / row.universe) * 100}%` }}
                      />
                      <span className="lb-bar__text">
                        {((row.solved / row.universe) * 100).toFixed(1)}%{' '}
                        <small>
                          ({row.solved}/{row.universe})
                        </small>
                      </span>
                    </div>
                  </td>
                  <td>
                    {row.successPct}% <small>({row.successFrac})</small>
                  </td>
                  <td>{row.timeSec.toLocaleString()}s</td>
                  <td className="lb__cost">
                    {row.cost == null ? '-' : `$${row.cost.toFixed(2)}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

function FixingSection() {
  return (
    <section className="section">
      <div className="container">
        <h2 id="bug-fixing">Bug Fixing Across Agents</h2>
        <p>
          Three coding agents patch all benchmark vulnerabilities under two
          modes: <strong>delta</strong>, where the bug-introducing commit is
          supplied, and <strong>full</strong>, where the CRS must localize the
          fault in the whole project on its own. Every patch must reproduce
          none of the PoVs (including CRSBench's variant PoVs) and pass the
          project's functionality tests.
        </p>
        <div className="lb__scroll">
          <table className="lb__table results-table">
            <thead>
              <tr>
                <th>CRS</th>
                <th>Delta mode</th>
                <th>Full mode</th>
                <th>Overall</th>
                <th>Time/trial</th>
                <th>LLM $/trial</th>
              </tr>
            </thead>
            <tbody>
              {FIXING_SUMMARY.map((row) => (
                <tr key={row.agent + row.model}>
                  <td>
                    <ModelIcon model={row.model} />
                    <strong>{row.agent}</strong>{' '}
                    <span className="lb__model">{row.model}</span>
                  </td>
                  <td>
                    <div className="lb-bar lb-bar--fix">
                      <span
                        className="lb-bar__fill"
                        style={{ width: `${row.delta.pct}%` }}
                      />
                      <span className="lb-bar__text">
                        {row.delta.pct}% <small>({row.delta.frac})</small>
                      </span>
                    </div>
                  </td>
                  <td>
                    <div className="lb-bar lb-bar--fix">
                      <span
                        className="lb-bar__fill"
                        style={{ width: `${row.full.pct}%` }}
                      />
                      <span className="lb-bar__text">
                        {row.full.pct}% <small>({row.full.frac})</small>
                      </span>
                    </div>
                  </td>
                  <td>{row.overallPct}%</td>
                  <td>{row.timeSec.toLocaleString()}s</td>
                  <td className="lb__cost">${row.cost.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <h3 id="patch-structure">Success rate by ground-truth patch structure</h3>
        <p>
          We bucket all 2,736 fixing trials by properties of the ground-truth
          patch: changed lines, files, hunks, and whether the top line of the
          crash stack trace matches the patched code. Success drops as patches
          grow or move away from the crash site, falling to 75–81% for patches
          with 3+ hunks, 14+ changed lines, or no match with the top line of
          the crash stack trace.
        </p>
        <DifficultyChart />
        <p className="results-backlink">
          <Link to="/crsbench">← Back to CRSBench overview</Link>
        </p>
      </div>
    </section>
  );
}

export default function CRSBenchResultsPage() {
  return (
    <Layout
      title="CRSBench Results"
      description="Evaluation results on CRSBench: end-to-end results of agent-based CRSs, bug-finding overlap between fuzzers, agents, and hybrids, and bug-fixing performance."
    >
      <Hero />
      <OverlapSection />
      <LeaderboardSection />
      <FixingSection />
    </Layout>
  );
}
