# Releasing Nexolith

Nexolith publishes tagged releases to PyPI with GitHub Actions and PyPI Trusted Publishing.
The release workflow exchanges a short-lived GitHub OIDC identity for a project-scoped PyPI token;
the repository must not contain a PyPI password or long-lived API token.

## One-time trusted publisher setup

Create a GitHub environment named `pypi` in the repository settings and configure:

- at least one required maintainer reviewer;
- prevention of self-review when more than one maintainer is available;
- deployment restricted to tags matching `v*`;
- administrator bypass disabled where repository policy permits it;
- no environment or repository PyPI secrets.

In the PyPI publishing settings for the `nexolith` project, add a GitHub trusted publisher with
these exact values:

| Setting | Value |
|---|---|
| Owner | `Reiver56` |
| Repository | `Nexolith` |
| Workflow | `release.yml` |
| Environment | `pypi` |

For a first publication where the PyPI project does not exist yet, register a pending publisher
with the same values. Do not add `PYPI_API_TOKEN`, a PyPI password, or an action input containing
credentials.

## Prepare a release

Release tags use the exact stable-version form `vX.Y.Z`. On a focused release-preparation branch:

1. Set the same `X.Y.Z` version in `project.version` in `pyproject.toml` and `__version__` in
   `src/nexolith/__init__.py`.
2. Change the matching `CHANGELOG.md` heading from `## [X.Y.Z] - Unreleased` to the real release
   date in ISO format, `## [X.Y.Z] - YYYY-MM-DD`.
3. Run the normal quality suite and the release validator:

   ```bash
   uv lock --check
   uv run ruff format --check .
   uv run ruff check .
   uv run mypy src
   uv run pytest
   uv run python -m nexolith.release_validation --tag vX.Y.Z
   ```

4. Merge the reviewed preparation into `master`. Create the tag only from that verified commit:

   ```bash
   git switch master
   git pull --ff-only
   git tag -a vX.Y.Z -m "Nexolith X.Y.Z"
   git push origin vX.Y.Z
   ```

The tag starts `.github/workflows/release.yml`. Its build job validates the tag, both version
declarations, the dated changelog section, wheel and source distribution metadata, and the exact
artifact set. It builds each distribution once and passes those immutable workflow artifacts to
the publishing job. A required reviewer must approve the `pypi` deployment before GitHub requests
the short-lived publishing credential.

Metadata validation runs once, immediately after the one-time build. The publishing action receives
that validated artifact set with its duplicate metadata check disabled; PyPI digital attestations
remain enabled.

## Failure and duplicate behavior

The workflow is intentionally fail-closed. Invalid tags, inconsistent versions, an unreleased or
missing changelog section, missing or extra distributions, and invalid package metadata stop before
publication. Concurrent runs for the same tag are serialized. The publisher does not enable
`skip-existing`, so a repeated or partially duplicated upload fails instead of silently reporting
success or replacing files on PyPI.

Do not move a published tag or attempt to overwrite published files. If nothing reached PyPI, fix
the release commit and create a new version tag. If a run may have uploaded only part of a release,
inspect PyPI and the workflow logs before taking action; use a new version for corrected artifacts.
