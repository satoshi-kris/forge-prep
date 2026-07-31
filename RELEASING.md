# Releasing

1. Bump `version` in `pyproject.toml`.
2. Update `CHANGELOG.md` with a new entry for the release.
3. Commit those changes.
4. Tag the release: `git tag vX.Y.Z` (must match `pyproject.toml`'s version exactly, with a leading `v` — e.g. version `0.2.0` → tag `v0.2.0`).
5. Push the tag: `git push origin vX.Y.Z`.
6. Watch the `Publish` workflow run in the Actions tab. It builds, checks, and publishes to PyPI via Trusted Publishing — no token to manage.

The workflow's version-guard step will fail the build (not the publish) if `pyproject.toml`'s version doesn't match the tag, so a mismatch is caught before anything reaches PyPI.

**PyPI versions are immutable.** Once `X.Y.Z` is published, it can never be re-uploaded or overwritten, even if it's broken. If a release goes out bad, bump the patch number and ship `X.Y.Z+1` — don't try to fix or delete the bad one.
