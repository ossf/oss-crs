// @ts-check
const { themes } = require('prism-react-renderer');

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: 'OSS-CRS',
  tagline: 'Cyber Reasoning Systems for Open Source Software',
  favicon: 'img/favicon.svg',

  url: 'https://oss-crs.openssf.org',
  baseUrl: '/',
  trailingSlash: false,

  organizationName: 'ossf',
  projectName: 'oss-crs',

  onBrokenLinks: 'warn',
  onBrokenMarkdownLinks: 'warn',

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  markdown: {
    format: 'detect',
  },

  presets: [
    [
      'classic',
      /** @type {import('@docusaurus/preset-classic').Options} */
      ({
        docs: {
          path: '../docs',
          routeBasePath: 'docs',
          sidebarPath: require.resolve('./sidebars.js'),
          include: ['**/*.md', '**/*.mdx'],
          exclude: ['**/node_modules/**'],
          editUrl: 'https://github.com/ossf/oss-crs/edit/main/docs/',
        },
        blog: false,
        theme: {
          customCss: require.resolve('./src/css/custom.css'),
        },
      }),
    ],
  ],

  plugins: [require.resolve('./plugins/registry-loader')],

  themeConfig:
    /** @type {import('@docusaurus/preset-classic').ThemeConfig} */
    ({
      colorMode: {
        defaultMode: 'light',
        respectPrefersColorScheme: true,
      },
      navbar: {
        title: 'OSS-CRS',
        items: [
          { to: '/#quick-start', label: 'Quick Start', position: 'left' },
          { to: '/registry', label: 'Registry', position: 'left' },
          { to: '/crsbench', label: 'CRSBench', position: 'left' },
          {
            type: 'docSidebar',
            sidebarId: 'docs',
            position: 'left',
            label: 'Docs',
          },
          {
            href: 'https://github.com/ossf/oss-crs',
            label: 'GitHub',
            position: 'right',
          },
        ],
      },
      footer: {
        style: 'dark',
        links: [
          {
            title: 'Docs',
            items: [
              { label: 'Docs index', to: '/docs/' },
              { label: 'Quick Start', to: '/#quick-start' },
              { label: 'CRS Registry', to: '/registry' },
              { label: 'CRSBench', to: '/crsbench' },
            ],
          },
          {
            title: 'Reference',
            items: [
              { label: 'Compose config', to: '/docs/config/crs-compose' },
              { label: 'CRS config', to: '/docs/config/crs' },
              { label: 'CRS development guide', to: '/docs/crs-development-guide' },
            ],
          },
          {
            title: 'More',
            items: [
              { label: 'GitHub', href: 'https://github.com/ossf/oss-crs' },
              { label: 'OSS-Fuzz', href: 'https://github.com/google/oss-fuzz' },
            ],
          },
        ],
        copyright: `Copyright © ${new Date().getFullYear()} OSS-CRS a Series of LF Projects, LLC. For web site terms of use, trademark policy and other project policies please see https://lfprojects.org/. Built with Docusaurus.`,
      },
      prism: {
        theme: themes.github,
        darkTheme: themes.dracula,
        additionalLanguages: ['bash', 'yaml', 'json', 'docker'],
      },
    }),
};

module.exports = config;
