import React from 'react';
import Layout from '@theme/Layout';
import Link from '@docusaurus/Link';
import CodeBlock from '@theme/CodeBlock';

const CRSBENCH_REPO = 'https://github.com/sslab-gatech/CRSBench';
const CRSBENCH_DATASET = 'https://huggingface.co/datasets/sslab-gatech/crsbench-dataset';

const QUICK_START = `git clone https://github.com/sslab-gatech/CRSBench.git && cd CRSBench
uv sync
./scripts/setup-third-party.sh

uv run crsbench prepare
uv run crsbench prepare --coverage

# Gated dataset: accept the DUA on HuggingFace first
uv run hf auth login
uv run crsbench download --benchmark-suite sanity`;

const FIRST_RUN_YAML = `experiment:
  name: first-run
  task: bugfinding
  mode: full
  benchmark_suite: sanity
  sanitizers: [address]

runtime:
  trials: 1
  max_total_time: 3600
  redis_host: localhost:6379
  litellm:
    skip: true

storage:
  experiment_filestore: ./results/experiment-data
  report_filestore: ./results/report-data

crs_compose:
  atlantis-multilang-given_fuzzer:
    num_cores: 4`;

const RUN_COMMANDS = `uv run python scripts/valkey-helper.py start

# Terminal 1: worker executes CRS trial jobs
uv run crsbench worker --experiment-config first-run.yaml

# Terminal 2: orchestrator enqueues jobs
uv run crsbench run --experiment-config first-run.yaml`;

const OVERVIEW_PILLARS = [
  {
    icon: '🧩',
    title: 'Supports every CRS',
    description:
      'Fuzzers, LLM agents, and hybrid systems run on the same sanitizer-based harness with the same resource limits. Any OSS-CRS-compatible CRS can run without changes.',
  },
  {
    icon: '🔁',
    title: 'Full-pipeline evaluation',
    description:
      'The framework takes the PoVs found by the bug-finding CRS and sends them to patching, so bug finding and patching are evaluated as one connected flow.',
  },
  {
    icon: '⚡',
    title: 'Faster evaluation',
    description:
      'Redis/RQ workers run trials across machines. Docker snapshot-based incremental builds skip full project rebuilds after each patch attempt, giving CRSs more tries within the same LLM budget.',
  },
  {
    icon: '⚙️',
    title: 'Production-style infra',
    description:
      'Pre-collected fuzzing corpora and Regression Test Selection (RTS) reflect the setup real deployments already maintain, so scores focus on CRS performance instead of infrastructure overhead.',
  },
];

const STATS = [
  { count: '124', label: 'Projects' },
  { count: '315', label: 'Vulnerabilities' },
  { count: '92', label: 'Unique CWEs' },
  { count: '21', label: 'of CWE Top 25 (2025)' },
  { count: 'C/C++, Java', label: 'Language' },
];

function GitHubLogo() {
  return (
    <svg className="button__brand-icon" viewBox="0 0 16 16" aria-hidden="true">
      <path
        fill="currentColor"
        d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82A7.65 7.65 0 0 1 8 3.87c.68 0 1.36.09 2 .26 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z"
      />
    </svg>
  );
}

function HuggingFaceLogo() {
  return (
    <span className="button__brand-icon button__brand-icon--huggingface" aria-hidden="true">
      🤗
    </span>
  );
}

function Hero() {
  return (
    <header className="hero hero--oss-crs">
      <div className="container text--center">
        <h1 className="hero__title">CRSBench</h1>
        <p className="hero__subtitle">
          A unified, full-pipeline benchmark for OSS-CRS.
        </p>
        <div className="hero__ctas">
          <a
            className="button button--primary button--lg"
            href={CRSBENCH_REPO}
            target="_blank"
            rel="noopener noreferrer"
          >
            <GitHubLogo />
            Code
          </a>
          <a
            className="button button--secondary button--lg"
            href={CRSBENCH_DATASET}
            target="_blank"
            rel="noopener noreferrer"
          >
            <HuggingFaceLogo />
            Dataset
          </a>
          <Link className="button button--outline button--lg" to="/crsbench#quick-start">
            Quick Start
          </Link>
        </div>
      </div>
    </header>
  );
}

function Overview() {
  return (
    <section className="section">
      <div className="container">
        <h2 id="overview">Overview</h2>
        <p>
          <strong>CRSBench</strong> is the benchmark suite for <Link to="/">OSS-CRS</Link>. It
          evaluates the full bug-finding and bug-fixing pipeline of any OSS-CRS-compatible CRS
          under production-style infrastructure (pre-collected corpora, incremental builds, RTS),
          and ships back into OSS-CRS as its standard evaluation framework.
        </p>
        <figure className="crsbench-figure">
          <img
            src="/img/crsbench/overview.svg"
            alt="CRSBench architecture: benchmark construction, builder, executor, and verifier."
            loading="lazy"
          />
        </figure>
        <div className="feature-grid">
          {OVERVIEW_PILLARS.map((p) => (
            <article key={p.title} className="feature-card">
              <span className="feature-card__icon" aria-hidden="true">{p.icon}</span>
              <h3>{p.title}</h3>
              <p>{p.description}</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

function Stats() {
  return (
    <section className="section section--alt">
      <div className="container">
        <h2 id="statistics">Statistics</h2>
        <p>
          CRSBench comprises C/C++ and Java projects with both manually curated synthetic
          vulnerabilities and real-world bugs, packaged with ground-truth
          PoVs, patches, and functionality tests.
        </p>
        <div className="hero-stats crsbench-stats">
          {STATS.map((s) => (
            <div key={s.label} className="hero-stat">
              <span className="hero-stat__count">{s.count}</span>
              <span className="hero-stat__label">{s.label}</span>
            </div>
          ))}
        </div>
        <div className="crsbench-figure-grid">
          <figure className="crsbench-figure">
            <img
              src="/img/crsbench/benchmark-cwes.svg"
              alt="CWE distribution donut chart across 124 benchmarks, 315 CPVs, 693 CWE entries."
              loading="lazy"
            />
          </figure>
          <figure className="crsbench-figure">
            <img
              src="/img/crsbench/benchmark-origins.svg"
              alt="CPVs by language (C/C++, JVM) and origin (1-day, synthetic)."
              loading="lazy"
            />
          </figure>
        </div>
      </div>
    </section>
  );
}

function QuickStart() {
  return (
    <section className="section">
      <div className="container">
        <h2 id="quick-start">Quick Start</h2>
        <p>
          CRSBench runs on Linux hosts with Docker. The smallest first run installs CRSBench, pulls
          the managed dependencies, downloads the <code>sanity</code> benchmark suite, and runs one
          experiment with a local queue-backed worker.
        </p>

        <h3>0. Request dataset access</h3>
        <p>
          The benchmark dataset on HuggingFace is gated. Before anything else, open{' '}
          <a href={CRSBENCH_DATASET} target="_blank" rel="noopener noreferrer">
            {CRSBENCH_DATASET.replace('https://', '')}
          </a>
          , request access, and wait for approval. Without it, <code>crsbench download</code> will
          fail.
        </p>

        <h3>1. Install and prepare</h3>
        <CodeBlock language="bash">{QUICK_START}</CodeBlock>

        <h3>2. Write a first-run config</h3>
        <p>
          Save the following as <code>first-run.yaml</code>. <code>atlantis-multilang-given_fuzzer</code>{' '}
          is the bundled starter CRS, and <code>litellm.skip: true</code> means no external LLM keys
          are required.
        </p>
        <CodeBlock language="yaml">{FIRST_RUN_YAML}</CodeBlock>

        <h3>3. Launch worker + orchestrator</h3>
        <CodeBlock language="bash">{RUN_COMMANDS}</CodeBlock>

        <p>
          The CRS lifecycle reuses <code>oss-crs prepare</code>, <code>oss-crs build-target</code>,{' '}
          <code>oss-crs artifacts</code>, and <code>oss-crs run</code>, so any CRS listed in the{' '}
          <Link to="/registry">OSS-CRS Registry</Link> plugs straight into <code>crs_compose</code>.
          For the distributed-experiment guide and configuration reference, see the upstream{' '}
          <a href={`${CRSBENCH_REPO}#readme`} target="_blank" rel="noopener noreferrer">
            README
          </a>{' '}
          and{' '}
          <a href={`${CRSBENCH_REPO}/tree/main/docs`} target="_blank" rel="noopener noreferrer">
            docs/
          </a>
          .
        </p>
      </div>
    </section>
  );
}

export default function CRSBenchPage() {
  return (
    <Layout
      title="CRSBench"
      description="CRSBench: a unified full-pipeline benchmark for Cyber Reasoning Systems, integrated with OSS-CRS."
    >
      <Hero />
      <Overview />
      <Stats />
      <QuickStart />
    </Layout>
  );
}
