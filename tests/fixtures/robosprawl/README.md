# Pinned downstream source

`source.tar.gz` contains the unmodified Python source, build metadata, license,
README, configuration, and composition test from `Tachion-Oy/robosprawl` commit
`7f956cb6cfd93db0cc1f95c7dcffc9eed0e7e8b5` (Apache-2.0; license in the archive).
The frontend and unrelated tests are omitted because the downstream gate does
not use them. This fixture is not included in any Roboz distribution.

The source archive lets the required downstream build, installed composition
tests, and HTTP workflow run on ordinary, Dependabot, and fork pull requests
without access to the private upstream repository or CI secrets. CI verifies
`SHA256SUMS` before extraction. The application and contract tests are identical
to those previously obtained by the cross-repository checkout.

To regenerate from a trusted local checkout, run from the Roboz repository root:

```bash
git -C ../robosprawl archive --format=tar.gz \
  --output="$PWD/tests/fixtures/robosprawl/source.tar.gz" \
  7f956cb6cfd93db0cc1f95c7dcffc9eed0e7e8b5 \
  LICENSE README.md pyproject.toml hub.config.json src \
  tests/unit/test_port_composition.py
(cd tests/fixtures/robosprawl && sha256sum source.tar.gz > SHA256SUMS)
```

For a pin update, review the upstream diff, update the revision here and in
`docs/build-and-test.md`, regenerate the archive and checksum, and run the full
downstream contract with fresh candidate wheels. Do not edit archived source.
