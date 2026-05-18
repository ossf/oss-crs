import React, { useMemo } from 'react';
import clsx from 'clsx';
import Link from '@docusaurus/Link';
import Layout from '@theme/Layout';
import CodeBlock from '@theme/CodeBlock';
import { usePluginData } from '@docusaurus/useGlobalData';

const STAT_TYPES = [
  { type: 'bug-finding', label: 'Bug finding' },
  { type: 'bug-fixing', label: 'Bug fixing' },
  { type: 'bug-finding-triage', label: 'Triage' },
  { type: 'bug-fixing-ensemble', label: 'Patch ensemble' },
  { type: 'seed-filter', label: 'Seed filter' },
];

function useRegistryStats() {
  const data = usePluginData('registry-loader') || {};
  const allEntries = Array.isArray(data.entries) ? data.entries : [];
  return useMemo(() => {
    const entries = allEntries.filter((e) => Boolean(e?.source?.url));
    const counts = new Map();
    for (const e of entries) {
      const types = Array.isArray(e.type) ? e.type : e.type ? [e.type] : [];
      for (const t of types) counts.set(t, (counts.get(t) || 0) + 1);
    }
    const known = STAT_TYPES.filter(({ type }) => counts.has(type)).map(({ type, label }) => ({
      type,
      label,
      count: counts.get(type),
    }));
    const otherTypes = [...counts.entries()]
      .filter(([t]) => !STAT_TYPES.some((s) => s.type === t))
      .sort();
    const other = otherTypes.map(([type, count]) => ({ type, label: type, count }));
    return { total: entries.length, breakdown: [...known, ...other] };
  }, [allEntries]);
}

function HeroStats() {
  const { total, breakdown } = useRegistryStats();
  if (total === 0) return null;
  return (
    <div className="hero-stats" aria-label="Registry statistics">
      <div className="hero-stat hero-stat--primary">
        <span className="hero-stat__count">{total}</span>
        <span className="hero-stat__label">Registered CRSs</span>
      </div>
      {breakdown.map(({ type, label, count }) => (
        <div
          key={type}
          className={`hero-stat hero-stat--${type.replace(/[^a-z0-9-]/gi, '-')}`}
        >
          <span className="hero-stat__count">{count}</span>
          <span className="hero-stat__label">{label}</span>
        </div>
      ))}
    </div>
  );
}

const FEATURES = [
  {
    icon: '⚡',
    title: 'Quick Start',
    description:
      'Run a baseline CRS against an OSS-Fuzz target in three commands. No LLM keys required for the libFuzzer baseline.',
    to: '/#quick-start',
    cta: 'Get started',
  },
  {
    icon: '🧩',
    title: 'Registry',
    description:
      'Browse certified CRSs — bug-finding, bug-fixing, triage, seed-filter — that drop into your compose file.',
    to: '/registry',
    cta: 'Browse CRSs',
  },
  {
    icon: '📚',
    title: 'Docs',
    description:
      'Compose & CRS configuration, target project setup, LLM routing, design notes, and the full CRS development guide.',
    to: '/docs/',
    cta: 'Read the docs',
  },
];

const PREPARE_TARGET = `git clone --depth=1 --filter=blob:none --no-checkout https://github.com/google/oss-fuzz.git
cd oss-fuzz
git sparse-checkout init --cone
git sparse-checkout set projects
git checkout
cd ..`;

const SETUP_CMD = `uv run oss-crs setup`;

const RUN_LIBFUZZER = `# Prepare the CRS
uv run oss-crs prepare \\
  --compose-file ./example/crs-libfuzzer/compose.yaml

# Build the target project
uv run oss-crs build-target \\
  --compose-file ./example/crs-libfuzzer/compose.yaml \\
  --fuzz-proj-path ./oss-fuzz/projects/libxml2

# Run the CRS against the "xml" harness
uv run oss-crs run \\
  --compose-file ./example/crs-libfuzzer/compose.yaml \\
  --fuzz-proj-path ./oss-fuzz/projects/libxml2 \\
  --target-harness xml`;

const RUN_LLM = `# export OPENAI_API_KEY=<your-openai-key>
# export GEMINI_API_KEY=<your-gemini-key>
# export ANTHROPIC_API_KEY=<your-anthropic-key>

uv run oss-crs prepare \\
  --compose-file ./example/atlantis-multilang-wo-concolic/compose.yaml

uv run oss-crs build-target \\
  --compose-file ./example/atlantis-multilang-wo-concolic/compose.yaml \\
  --fuzz-proj-path ./oss-fuzz/projects/libxml2

uv run oss-crs run \\
  --compose-file ./example/atlantis-multilang-wo-concolic/compose.yaml \\
  --fuzz-proj-path ./oss-fuzz/projects/libxml2 \\
  --target-harness xml`;

function Hero() {
  return (
    <header className="hero hero--oss-crs">
      <div className="container text--center">
        <h1 className="hero__title">OSS-CRS</h1>
        <p className="hero__subtitle">
          Cyber Reasoning Systems for Bug-Finding and Patching<br />in Open Source Software
        </p>
        <div className="hero__ctas">
          <Link className="button button--primary button--lg" to="/#quick-start">
            Quick Start
          </Link>
          <Link className="button button--secondary button--lg" to="/registry">
            Browse Registry
          </Link>
          <Link className="button button--outline button--lg" to="/docs/">
            Read Docs
          </Link>
        </div>
        <HeroStats />
      </div>
    </header>
  );
}

function FeatureCards() {
  return (
    <section className="section">
      <div className="container">
        <div className="feature-grid">
          {FEATURES.map((f) => (
            <Link key={f.title} to={f.to} className="feature-card" style={{textDecoration: 'none', color: 'inherit'}}>
              <span className="feature-card__icon" aria-hidden="true">{f.icon}</span>
              <h3>{f.title}</h3>
              <p>{f.description}</p>
              <span className="button button--sm button--primary">{f.cta} →</span>
            </Link>
          ))}
        </div>
      </div>
    </section>
  );
}

function QuickStart() {
  return (
    <section className="section section--alt">
      <div className="container">
        <h2>Overview</h2>
        <p>
          OSS-CRS is an open framework for running Cyber Reasoning Systems against OSS-Fuzz-style targets.
          A CRS is an agentic system that combines LLM reasoning with traditional security techniques to autonomously find and patch bugs in software.
          OSS-CRS provides a unified CLI and compose file interface for orchestrating bug-finding fuzzers, LLM-powered bug-fixing agents, triage systems, and seed generators,
          letting you mix and match components from the registry or bring your own, all against the same target project.
        </p>
        <h2 id="quick-start">Quick Start</h2>
        <p>
          OSS-CRS works with projects that follow the{' '}
          <a href="https://github.com/google/oss-fuzz" target="_blank" rel="noopener noreferrer">
            OSS-Fuzz
          </a>{' '}
          project structure. Make sure you have <strong>Python 3.10+</strong>, <strong>Docker</strong>, <strong>git</strong>, and{' '}
          <a href="https://github.com/astral-sh/uv" target="_blank" rel="noopener noreferrer"><code>uv</code></a>{' '}
          installed.
        </p>

        <h3>1. Clone OSS-Fuzz for project environments</h3>
        <CodeBlock language="bash">{PREPARE_TARGET}</CodeBlock>

        <h3>2. Set up your machine (optional)</h3>
        <p>
          Configures LLM provider credentials and system resource isolation.
          Both steps are interactive and can be skipped.
        </p>
        <CodeBlock language="bash">{SETUP_CMD}</CodeBlock>

        <h3>3. Run a baseline CRS</h3>
        <p>
          <code>crs-libfuzzer</code> is a lightweight CRS that runs libFuzzer on the target. It needs no
          LLM credentials and is a good baseline.
        </p>
        <CodeBlock language="bash">{RUN_LIBFUZZER}</CodeBlock>

        <h3>4. Run an LLM-backed CRS</h3>
        <p>
          For LLM-backed CRSs, export provider keys (or put them in <code>.env</code>) and use one of the
          multi-language Atlantis examples.
        </p>
        <CodeBlock language="bash">{RUN_LLM}</CodeBlock>
        <p>
          See <Link to="/docs/config/llm">LLM configuration</Link> for full provider details.
        </p>

      </div>
    </section>
  );
}

function OpenSSFAttribution() {
  return (
    <section className="section section--openssf">
      <div className="container openssf-attribution">
        <a
          href="https://openssf.org/"
          target="_blank"
          rel="noopener noreferrer"
          className="openssf-attribution__logo"
          aria-label="OpenSSF"
        >
          <img src="/img/openssf-horizontal-white.png" alt="OpenSSF" />
        </a>
        <a
          href="https://openssf.org/projects/"
          target="_blank"
          rel="noopener noreferrer"
          className="openssf-attribution__badge"
          aria-label="OpenSSF Sandbox project"
        >
          <img src="/img/openssf-sandbox.png" alt="OpenSSF Sandbox project" />
        </a>
        <p className="openssf-attribution__text">
          OSS-CRS is a sandbox project in the OpenSSF
        </p>
      </div>
    </section>
  );
}

function NextSteps() {
  return (
    <section className="section">
      <div className="container">
        <h2>Next steps</h2>
        <ul>
          <li>
            <Link to="/registry">Browse the CRS Registry</Link> — pick a registered CRS to plug into your compose file.
          </li>
          <li>
            <Link to="/docs/crs-development-guide">Build your own CRS</Link> — package a bug-finding or bug-fixing tool
            behind the OSS-CRS lifecycle.
          </li>
          <li>
            <Link to="/docs/setup">Host setup</Link> — configure LLM proxy routing and cgroup resource isolation.
          </li>
          <li>
            <Link to="/docs/design/architecture">Architecture notes</Link> — main components and lifecycle.
          </li>
        </ul>
      </div>
    </section>
  );
}

export default function Home() {
  return (
    <Layout
      title="OSS-CRS — Cyber Reasoning Systems for Open Source Software"
      description="OSS-CRS orchestrates autonomous bug-finding, bug-fixing, triage, and ensemble Cyber Reasoning Systems against OSS-Fuzz-style targets."
    >
      <Hero />
      <FeatureCards />
      <QuickStart />
      <NextSteps />
      <OpenSSFAttribution />
    </Layout>
  );
}
