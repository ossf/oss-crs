const fs = require('node:fs');
const path = require('node:path');
const yaml = require('js-yaml');

const TYPE_LABELS = {
  'bug-finding': 'Bug finding',
  'bug-fixing': 'Bug fixing',
  'bug-finding-triage': 'Bug-finding triage',
  'bug-fixing-ensemble': 'Bug-fixing ensemble',
  'seed-filter': 'Seed filter',
};

function loadYamlSafe(absPath) {
  try {
    const text = fs.readFileSync(absPath, 'utf-8');
    return yaml.load(text);
  } catch (err) {
    console.warn(`[registry-loader] failed to parse ${absPath}: ${err.message}`);
    return null;
  }
}

function collectEntries(registryDir, repoRoot) {
  const entries = [];
  for (const dirent of fs.readdirSync(registryDir, { withFileTypes: true })) {
    if (dirent.isFile() && dirent.name.endsWith('.yaml')) {
      const filePath = path.join(registryDir, dirent.name);
      const data = loadYamlSafe(filePath);
      if (!data || typeof data !== 'object') continue;
      entries.push({
        ...data,
        manifestPath: path.relative(repoRoot, filePath),
      });
    } else if (dirent.isDirectory()) {
      const pkgPath = path.join(registryDir, dirent.name, 'pkg.yaml');
      if (!fs.existsSync(pkgPath)) continue;
      const data = loadYamlSafe(pkgPath);
      if (!data || typeof data !== 'object') continue;
      entries.push({
        ...data,
        manifestPath: path.relative(repoRoot, pkgPath),
      });
    }
  }
  return entries.sort((a, b) =>
    String(a.name || '').localeCompare(String(b.name || '')),
  );
}

module.exports = function registryLoaderPlugin(context) {
  return {
    name: 'registry-loader',

    async loadContent() {
      const repoRoot = path.resolve(context.siteDir, '..');
      const registryDir = path.join(repoRoot, 'registry');
      if (!fs.existsSync(registryDir)) {
        return { entries: [], registryDir: null };
      }
      const entries = collectEntries(registryDir, repoRoot);
      return {
        entries,
        registryDir: path.relative(repoRoot, registryDir),
        typeLabels: TYPE_LABELS,
      };
    },

    async contentLoaded({ content, actions }) {
      actions.setGlobalData(content);
    },

    getPathsToWatch() {
      const repoRoot = path.resolve(context.siteDir, '..');
      return [path.join(repoRoot, 'registry/**/*.yaml')];
    },
  };
};
