# Contributing to OSS-CRS

Thank you for your interest and time in contributing to OSS-CRS!
For first steps you can run one of our currently available CRSs on real world projects, or try developing/integrating your own CRS.
We'd be glad accepting feedback on either use case.

We are also currently discussing architectural changes, and you are more than welcome to follow such discussions in our Github Issues.

## Governance

### Technical Steering Committee (TSC)

The Technical Steering Committee (TSC) is responsible for all technical oversight of the OSS-CRS project. TSC voting members are the project's Maintainers. Decisions are made by consensus when possible; when a vote is needed, each voting member has one vote and a majority of those present (with quorum) is required.

### Roles

**Contributors** are anyone in the technical community who contributes code, documentation, or other technical artifacts to the project.

**Maintainers** are Contributors who have earned the ability to approve and merge changes to the project's repositories. A Contributor may become a Maintainer by a majority approval of the TSC. Maintainers serve as the TSC voting members.

### Initial Maintainers (alphabetic order)

The following individuals are the initial Maintainers and TSC voting members of the project:

| Name              | Organization                          | GitHub        |
|-------------------|---------------------------------------|---------------|
| Andrew Chin       | Georgia Institute of Technology       | @azchin       |
| Cen Zhang         | Georgia Institute of Technology       | @occia        |
| Dongkwan Kim      | Georgia Institute of Technology       | @0xdkay       |
| Fabian Fleischer  | Georgia Institute of Technology       | @fab1ano      |
| Hanqing Zhao      | Georgia Institute of Technology       | @hq1995       |
| HyungSeok Han     | Microsoft                             | @DaramG       |
| Jiho Kim          | Georgia Institute of Technology       | @jhkimx2      |
| Taesoo Kim        | Georgia Institute of Technology & Microsoft | @tsgates |
| Younggi Park      | Independent Researcher                | @grill66      |
| Youngjoon Kim     | Georgia Institute of Technology       | @acorn421     |
| Yu-Fu Fu          | Georgia Institute of Technology       | @fuyu0425     |

## Reporting Issues

We use the [Github issue tracker](https://github.com/sslab-gatech/oss-crs/issues) for tracking our tasks and bugs.
When reporting, please include:

### Observed and Expected Behavior

What you see v.s. what you expected to see.
This includes build errors, faulty runtime behavior, or deviations from our specification.

### Reproduction Steps

The list of commands run to trigger such behavior.
Usage of OSS-CRS heavily relies on state set up by different commands,
and so it is essential for us to recreate such state (directories, images, etc.).

### Environment

Any other information about your environment would help. Things like a differently configured LiteLLM proxy or Docker may contribute to issues we have not seen in our development environment.

## Contributing Code

If you have a feature or fix that you want to contribute, branch off main and create a pull request when ready!

Ideally if you are the only developer of said branch, please [rebase](https://git-scm.com/book/en/v2/Git-Branching-Rebasing) from main before creating your PR to keep git history clean.

For commit messages, we use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) as our standard.
`fix:`, `feat:`, `chore:`, `docs:`, and `refactor:` are types commonly used.

When you create a PR, assign a reviewer (typically @azchin).

### Changelog and release-note policy

For user-facing changes (CLI flags, config behavior, API contracts, runtime
semantics, or migration-impacting docs), add an entry to `CHANGELOG.md` under
`[Unreleased]`.

We follow [Common Changelog](https://common-changelog.org/) (lightly, to keep
contributor overhead low) for release notes.

When the change is deprecating or breaking:
- clearly mark it in the changelog
- provide the replacement path
- include planned removal timing/version when known

### Developer Certificate of Origin (DCO)

All contributions to this project must be accompanied by a Developer Certificate of Origin sign-off. The DCO is a lightweight mechanism to certify that you wrote or have the right to submit the code you are contributing. The full text is available at [developercertificate.org](http://developercertificate.org).

You sign off by adding a `Signed-off-by` line to your commit messages:

```
Signed-off-by: Your Name <your.email@example.com>
```

This can be done automatically by passing the `-s` flag to `git commit`:

```
git commit -s -m "feat: add new CRS integration"
```

## Code of Conduct

This project follows the [LF Projects Code of Conduct](https://lfprojects.org/policies/code-of-conduct/). Please report any unacceptable behavior to the TSC or the Series Manager.
