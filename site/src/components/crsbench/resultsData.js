/**
 * Evaluation results from the CRSBench paper.
 *
 * All numbers are computed from the paper's evaluation data
 * (3 trials per task, $30 LLM budget, 8h bug-finding / 2h bug-fixing,
 * 16 cores + 64 GB RAM per CRS).
 */

export const SETUP_STATS = [
  { count: '3', label: 'Trials per task' },
  { count: '$30', label: 'LLM budget / trial' },
  { count: '8h / 2h', label: 'Find / fix time limit' },
  { count: '245K', label: 'CPU-hours' },
  { count: '$31K', label: 'Total eval spend' },
];

// RQ1: bug finding on 117 benchmarks / 304 CPVs.
export const FINDING_SUMMARY = [
  {
    crs: 'Fuzzer only',
    id: 'crs-given-fuzzer',
    trials: 921,
    solved: 80,
    universe: 304,
    successPct: 24.6,
    successFrac: '227/921',
    timeSec: 1668,
    cost: null,
  },
  {
    crs: 'LLM agent',
    id: 'crs-claude-code (Opus 4.6)',
    trials: 921,
    solved: 244,
    universe: 304,
    successPct: 62.9,
    successFrac: '579/921',
    timeSec: 1981,
    cost: 17.07,
  },
];

// Same summary restricted to the hard 28-benchmark / 95-CPV subset,
// where crs-hybrid was additionally evaluated.
export const FINDING_SUMMARY_HARD = [
  {
    crs: 'Fuzzer only',
    id: 'crs-given-fuzzer',
    trials: 294,
    solved: 13,
    universe: 95,
    successPct: 12.2,
    successFrac: '36/294',
    timeSec: 3854,
    cost: null,
  },
  {
    crs: 'LLM agent',
    id: 'crs-claude-code (Opus 4.6)',
    trials: 294,
    solved: 35,
    universe: 95,
    successPct: 28.6,
    successFrac: '84/294',
    timeSec: 2178,
    cost: 18.32,
  },
  {
    crs: 'Hybrid',
    id: 'crs-hybrid (fuzzer + Claude Code)',
    trials: 294,
    solved: 47,
    universe: 95,
    successPct: 38.8,
    successFrac: '114/294',
    timeSec: 1922,
    cost: 17.26,
  },
];

// UpSet membership classes over all 304 CPVs.
// bits order matches `sets` order; '1' = found by that configuration.
export const UPSET_FULL = {
  universe: 304,
  unit: 'CPVs',
  sets: [
    { key: 'fuzzer', label: 'Fuzzer', total: 80 },
    { key: 'agent', label: 'LLM agent', total: 244 },
  ],
  intersections: [
    { bits: '01', count: 170 },
    { bits: '11', count: 74 },
    { bits: '10', count: 6 },
    { bits: '00', count: 54 },
  ],
};

// UpSet membership classes on the hard 28-benchmark / 95-CPV subset
// where neither the fuzzer nor the agent found every vulnerability.
export const UPSET_HYBRID = {
  universe: 95,
  unit: 'CPVs',
  sets: [
    { key: 'fuzzer', label: 'Fuzzer', total: 13 },
    { key: 'agent', label: 'LLM agent', total: 35 },
    { key: 'hybrid', label: 'Hybrid', total: 47 },
  ],
  intersections: [
    { bits: '011', count: 23 },
    { bits: '001', count: 12 },
    { bits: '111', count: 7 },
    { bits: '101', count: 5 },
    { bits: '010', count: 5 },
    { bits: '100', count: 1 },
    { bits: '000', count: 42 },
  ],
};

// RQ2: end-to-end results on the partial AFC subset (51 vulnerabilities).
// Success counts vulnerabilities solved at least once across 3 trials;
// cost is mean per-trial LLM spend. Fix is conditional on Find.
export const LEADERBOARD = [
  {
    agent: 'Claude Code',
    model: 'Opus 4.6',
    find: { n: 47, d: 51, cost: 10.91 },
    fix: { n: 42, d: 47, cost: 1.19 },
    e2e: { n: 42, d: 51, cost: 9.52 },
  },
  {
    agent: 'Opencode',
    model: 'GLM-5.1',
    find: { n: 43, d: 51, cost: 2.11 },
    fix: { n: 35, d: 43, cost: 0.12 },
    e2e: { n: 35, d: 51, cost: 1.73 },
  },
  {
    agent: 'Gemini CLI',
    model: 'Gemini 3 Flash',
    find: { n: 31, d: 51, cost: 1.77 },
    fix: { n: 29, d: 31, cost: 0.35 },
    e2e: { n: 29, d: 51, cost: 1.55 },
  },
  {
    agent: 'Codex',
    model: 'GPT-5.4-mini',
    find: { n: 30, d: 51, cost: 1.26 },
    fix: { n: 28, d: 30, cost: 0.36 },
    e2e: { n: 28, d: 51, cost: 1.15 },
  },
  {
    agent: 'Claude Code',
    model: 'Haiku 4.5',
    find: { n: 23, d: 51, cost: 1.09 },
    fix: { n: 16, d: 23, cost: 0.42 },
    e2e: { n: 16, d: 51, cost: 0.91 },
  },
];

// RQ1: bug-fixing trial outcomes bucketed by ground-truth patch
// difficulty (2,736 trials: 3 agents x 912 tasks). `site` compares the
// top line of the crash stack trace with the ground-truth patch.
export const DIFFICULTY_OUTCOMES = [
  { key: 'success', label: 'Success', color: '#59A14E' },
  { key: 'func_fail', label: 'Func. test fail', color: '#F28E2B' },
  { key: 'security_fail', label: 'Security fail', color: '#E15759' },
  { key: 'no_patch_build', label: 'No patch / build fail', color: '#BAB0AB' },
];

export const DIFFICULTY_PANELS = [
  { key: 'lines', title: 'Changed lines', bins: ['0-2', '3-6', '7-13', '14+'] },
  { key: 'files', title: 'Files changed', bins: ['1', '2', '3+'] },
  { key: 'hunks', title: 'Hunks', bins: ['1', '2', '3+'] },
  {
    key: 'site',
    title: 'Crash stack top line / patch match',
    bins: ['line', 'function', 'file', 'no match'],
  },
];

export const DIFFICULTY_AGENTS = [
  { key: 'all', label: 'All agents' },
  { key: 'crs-claude-code', label: 'Claude Code (Opus 4.6)' },
  { key: 'crs-codex', label: 'Codex (GPT-5.4)' },
  { key: 'crs-gemini-cli', label: 'Gemini CLI (Gemini 3.1 Pro)' },
];

// counts[agent][panel][bin] = {success, func_fail, security_fail, no_patch_build}
export const DIFFICULTY_DATA = {
  all: {
    lines: {
      '0-2': { success: 658, func_fail: 18, security_fail: 68, no_patch_build: 30 },
      '3-6': { success: 581, func_fail: 17, security_fail: 19, no_patch_build: 31 },
      '7-13': { success: 796, func_fail: 39, security_fail: 21, no_patch_build: 8 },
      '14+': { success: 338, func_fail: 45, security_fail: 56, no_patch_build: 11 },
    },
    files: {
      1: { success: 1712, func_fail: 108, security_fail: 143, no_patch_build: 71 },
      2: { success: 607, func_fail: 10, security_fail: 14, no_patch_build: 8 },
      '3+': { success: 54, func_fail: 1, security_fail: 7, no_patch_build: 1 },
    },
    hunks: {
      1: { success: 1246, func_fail: 44, security_fail: 96, no_patch_build: 54 },
      2: { success: 802, func_fail: 52, security_fail: 10, no_patch_build: 18 },
      '3+': { success: 325, func_fail: 23, security_fail: 58, no_patch_build: 8 },
    },
    site: {
      line: { success: 723, func_fail: 64, security_fail: 44, no_patch_build: 15 },
      function: { success: 806, func_fail: 12, security_fail: 43, no_patch_build: 39 },
      file: { success: 524, func_fail: 23, security_fail: 33, no_patch_build: 14 },
      'no match': { success: 320, func_fail: 20, security_fail: 44, no_patch_build: 12 },
    },
  },
  'crs-claude-code': {
    lines: {
      '0-2': { success: 216, func_fail: 7, security_fail: 28, no_patch_build: 7 },
      '3-6': { success: 194, func_fail: 6, security_fail: 8, no_patch_build: 8 },
      '7-13': { success: 267, func_fail: 14, security_fail: 7, no_patch_build: 0 },
      '14+': { success: 110, func_fail: 17, security_fail: 21, no_patch_build: 2 },
    },
    files: {
      1: { success: 567, func_fail: 42, security_fail: 54, no_patch_build: 15 },
      2: { success: 203, func_fail: 2, security_fail: 7, no_patch_build: 1 },
      '3+': { success: 17, func_fail: 0, security_fail: 3, no_patch_build: 1 },
    },
    hunks: {
      1: { success: 414, func_fail: 17, security_fail: 38, no_patch_build: 11 },
      2: { success: 265, func_fail: 19, security_fail: 6, no_patch_build: 4 },
      '3+': { success: 108, func_fail: 8, security_fail: 20, no_patch_build: 2 },
    },
    site: {
      line: { success: 243, func_fail: 22, security_fail: 15, no_patch_build: 2 },
      function: { success: 271, func_fail: 5, security_fail: 14, no_patch_build: 10 },
      file: { success: 168, func_fail: 10, security_fail: 16, no_patch_build: 4 },
      'no match': { success: 105, func_fail: 7, security_fail: 19, no_patch_build: 1 },
    },
  },
  'crs-codex': {
    lines: {
      '0-2': { success: 219, func_fail: 5, security_fail: 23, no_patch_build: 11 },
      '3-6': { success: 195, func_fail: 6, security_fail: 4, no_patch_build: 11 },
      '7-13': { success: 265, func_fail: 15, security_fail: 6, no_patch_build: 2 },
      '14+': { success: 117, func_fail: 14, security_fail: 19, no_patch_build: 0 },
    },
    files: {
      1: { success: 576, func_fail: 33, security_fail: 47, no_patch_build: 22 },
      2: { success: 201, func_fail: 6, security_fail: 4, no_patch_build: 2 },
      '3+': { success: 19, func_fail: 1, security_fail: 1, no_patch_build: 0 },
    },
    hunks: {
      1: { success: 417, func_fail: 13, security_fail: 31, no_patch_build: 19 },
      2: { success: 268, func_fail: 19, security_fail: 2, no_patch_build: 5 },
      '3+': { success: 111, func_fail: 8, security_fail: 19, no_patch_build: 0 },
    },
    site: {
      line: { success: 244, func_fail: 21, security_fail: 15, no_patch_build: 2 },
      function: { success: 265, func_fail: 5, security_fail: 17, no_patch_build: 13 },
      file: { success: 181, func_fail: 5, security_fail: 8, no_patch_build: 4 },
      'no match': { success: 106, func_fail: 9, security_fail: 12, no_patch_build: 5 },
    },
  },
  'crs-gemini-cli': {
    lines: {
      '0-2': { success: 223, func_fail: 6, security_fail: 17, no_patch_build: 12 },
      '3-6': { success: 192, func_fail: 5, security_fail: 7, no_patch_build: 12 },
      '7-13': { success: 264, func_fail: 10, security_fail: 8, no_patch_build: 6 },
      '14+': { success: 111, func_fail: 14, security_fail: 16, no_patch_build: 9 },
    },
    files: {
      1: { success: 569, func_fail: 33, security_fail: 42, no_patch_build: 34 },
      2: { success: 203, func_fail: 2, security_fail: 3, no_patch_build: 5 },
      '3+': { success: 18, func_fail: 0, security_fail: 3, no_patch_build: 0 },
    },
    hunks: {
      1: { success: 415, func_fail: 14, security_fail: 27, no_patch_build: 24 },
      2: { success: 269, func_fail: 14, security_fail: 2, no_patch_build: 9 },
      '3+': { success: 106, func_fail: 7, security_fail: 19, no_patch_build: 6 },
    },
    site: {
      line: { success: 236, func_fail: 21, security_fail: 14, no_patch_build: 11 },
      function: { success: 270, func_fail: 2, security_fail: 12, no_patch_build: 16 },
      file: { success: 175, func_fail: 8, security_fail: 9, no_patch_build: 6 },
      'no match': { success: 109, func_fail: 4, security_fail: 13, no_patch_build: 6 },
    },
  },
};

// RQ1: bug fixing across the full benchmark, split by challenge mode.
// Delta mode supplies the bug-introducing commit; full mode does not.
export const FIXING_SUMMARY = [
  {
    agent: 'Claude Code',
    model: 'Opus 4.6',
    delta: { pct: 88.3, frac: '546/618' },
    full: { pct: 82.0, frac: '241/294' },
    overallPct: 86.3,
    timeSec: 607,
    cost: 1.43,
  },
  {
    agent: 'Codex',
    model: 'GPT-5.4',
    delta: { pct: 88.0, frac: '544/618' },
    full: { pct: 85.7, frac: '252/294' },
    overallPct: 87.3,
    timeSec: 589,
    cost: 1.29,
  },
  {
    agent: 'Gemini CLI',
    model: 'Gemini 3.1 Pro',
    delta: { pct: 87.9, frac: '543/618' },
    full: { pct: 84.0, frac: '247/294' },
    overallPct: 86.6,
    timeSec: 1255,
    cost: 0.89,
  },
];
