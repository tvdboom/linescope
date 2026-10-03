# Releasing

The initial repository is configured for `tvdboom/linescope`. Documentation and package URLs
become live after the maintainer creates the corresponding release/deployment. Configuration
alone does not publish a package or website.

## One-time repository setup

- Enable GitHub Pages for the branch used by `mike` (normally `gh-pages`).
- Configure the `github-pages` and `pypi` environments with appropriate release protection.
- Register a PyPI trusted publisher for this repository's `publish.yml` workflow.
- Optionally add the Codecov token for coverage uploads.

## Release process

Update the version in `pyproject.toml` and the package version, refresh `uv.lock`, and run the
full test/documentation/build checks. Review release notes and build artifacts before creating
a matching `vX.Y.Z` tag.

The tag workflow validates the version, checks the package, builds one universal Python wheel
and source distribution, then uses trusted publishing. It also deploys versioned docs with
`mike` and updates the `latest` alias. Pure Python wheels need no per-platform Rust builds.

No release tag, package publication, or documentation deployment is part of ordinary local
development. Run `uv build` and inspect `dist` to validate an artifact locally.
