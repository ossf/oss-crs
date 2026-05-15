import React, { useMemo, useState } from 'react';
import Layout from '@theme/Layout';
import Link from '@docusaurus/Link';
import { usePluginData } from '@docusaurus/useGlobalData';

const REGISTRY_GITHUB_BASE = 'https://github.com/ossf/oss-crs/tree/main/';

function typeClass(type) {
  return `registry-tag registry-tag--${String(type).replace(/[^a-z0-9-]/gi, '-')}`;
}

function gitBranchUrl(source) {
  if (!source?.url) return null;
  const base = source.url.replace(/\.git$/, '');
  return source.ref ? `${base}/tree/${source.ref}` : base;
}

function orgRepo(url) {
  const cleaned = url.replace(/\.git$/, '').replace(/\/$/, '');
  const parts = cleaned.split('/').filter(Boolean);
  if (parts.length < 2) return cleaned;
  return `${parts[parts.length - 2]}/${parts[parts.length - 1]}`;
}

function SourceLine({ source }) {
  const href = gitBranchUrl(source);
  if (!href || !source?.url) return null;
  const label = orgRepo(source.url);
  return (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {label}
      {source.ref ? <> @ <code>{source.ref}</code></> : null}
    </a>
  );
}

function RegistryCard({ entry, typeLabels }) {
  const types = Array.isArray(entry.type) ? entry.type : entry.type ? [entry.type] : [];
  return (
    <article className="registry-card">
      <h4>{entry.name}</h4>
      <div className="registry-card__types">
        {types.length === 0 ? (
          <span className="registry-tag">untyped</span>
        ) : (
          types.map((t) => (
            <span key={t} className={typeClass(t)}>
              {typeLabels?.[t] || t}
            </span>
          ))
        )}
      </div>
      <div className="registry-card__source">
        <SourceLine source={entry.source} />
      </div>
      {entry.manifestPath ? (
        <div className="registry-card__manifest">
          <a
            href={`${REGISTRY_GITHUB_BASE}${entry.manifestPath}`}
            target="_blank"
            rel="noopener noreferrer"
          >
            {entry.manifestPath}
          </a>
        </div>
      ) : null}
    </article>
  );
}

export default function RegistryPage() {
  const data = usePluginData('registry-loader') || {};
  const allEntries = Array.isArray(data.entries) ? data.entries : [];
  const entries = useMemo(
    () => allEntries.filter((e) => Boolean(e?.source?.url)),
    [allEntries],
  );
  const typeLabels = data.typeLabels || {};

  const allTypes = useMemo(() => {
    const s = new Set();
    for (const e of entries) {
      const types = Array.isArray(e.type) ? e.type : e.type ? [e.type] : [];
      types.forEach((t) => s.add(t));
    }
    return Array.from(s).sort();
  }, [entries]);

  const [query, setQuery] = useState('');
  const [activeType, setActiveType] = useState(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return entries.filter((entry) => {
      const types = Array.isArray(entry.type) ? entry.type : entry.type ? [entry.type] : [];
      if (activeType && !types.includes(activeType)) return false;
      if (!q) return true;
      const haystack = [
        entry.name,
        entry.manifestPath,
        entry.source?.url,
        entry.source?.local_path,
        entry.source?.ref,
        types.join(' '),
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [entries, query, activeType]);

  return (
    <Layout
      title="CRS Registry"
      description="Browse the catalog of Cyber Reasoning Systems registered for use with OSS-CRS."
    >
      <main className="container margin-vert--lg">
        <header className="margin-bottom--md">
          <h1>CRS Registry</h1>
          <p>
            Registered Cyber Reasoning Systems compatible with the OSS-CRS framework. Reference any
            of these by <code>name</code> in your <Link to="/docs/config/crs-compose">compose file</Link>{' '}
            — OSS-CRS resolves the source automatically. See the{' '}
            <Link to="/docs/registry">registry guide</Link> for usage and the{' '}
            <Link to="/docs/crs-development-guide">CRS development guide</Link> to register your own.
          </p>
        </header>

        {entries.length === 0 ? (
          <div className="registry-empty">
            No registry entries found. (Looked under <code>registry/</code> in the repo root.)
          </div>
        ) : (
          <>
            <div className="registry-controls">
              <input
                type="search"
                className="registry-search"
                placeholder={`Search ${entries.length} CRSs by name, type, or source…`}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label="Search registry"
              />
              <button
                type="button"
                className={`registry-filter${activeType === null ? ' registry-filter--active' : ''}`}
                onClick={() => setActiveType(null)}
              >
                All
              </button>
              {allTypes.map((t) => (
                <button
                  key={t}
                  type="button"
                  className={`registry-filter${activeType === t ? ' registry-filter--active' : ''}`}
                  onClick={() => setActiveType(t === activeType ? null : t)}
                >
                  {typeLabels[t] || t}
                </button>
              ))}
            </div>

            <p className="margin-bottom--md">
              Showing <strong>{filtered.length}</strong> of {entries.length}.
            </p>

            {filtered.length === 0 ? (
              <div className="registry-empty">No CRSs match your filters.</div>
            ) : (
              <div className="registry-grid">
                {filtered.map((entry) => (
                  <RegistryCard
                    key={entry.manifestPath || entry.name}
                    entry={entry}
                    typeLabels={typeLabels}
                  />
                ))}
              </div>
            )}
          </>
        )}
      </main>
    </Layout>
  );
}
