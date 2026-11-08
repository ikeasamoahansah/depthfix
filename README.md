# depthfix

**Deprecation registry scanner and fixer** — scans your project's dependency
manifests for deprecated / end-of-life / outdated packages and rewrites them to
supported versions (with the successor package where one exists).

## What it does

```console
$ depthfix --check .
depthfix 0.1.0 — deprecation registry & fixer
scanning: /home/you/project  [check]

/home/you/project/package.json
  DEPRECATED  node-sass ^7.0.3  [dependencies] (line 5)
      LibSass and node-sass are deprecated; the maintainer recommends Dart Sass.  (EOL 2024-04-30)
      fix:  node-sass → sass, '^7.0.3' → '^1.108.1'  [applied]
  DEPRECATED  request 2.88.2  [dependencies] (line 6)
      request is fully deprecated; no new versions will be published.  (EOL 2020-02-11)
      advisory only, no automatic migration — consider: got, axios, native fetch

summary: 2 deprecated · 0 outdated · 1 fixed · 1 need attention
```

* **Find**: reads manifests, resolves each dependency against a *deprecation
  registry* (bundled static registry first, live registry APIs second).
* **Fix**: rewrites manifests in place, preserving formatting — punctuation,
  comments, key order and style all survive. Where a deprecated package has a
  designated successor (`node-sass → sass`, `pycrypto → pycryptodome`,
  `gopkg.in/yaml.v2 → gopkg.in/yaml.v3`, …) the package is swapped; otherwise
  the same package is bumped to its latest supported version.
* **Install**: after rewriting, runs the right package manager
  (`npm install`, `pip install -r requirements.txt`, `uv sync` / `poetry
  install`, `go get … && go mod tidy`, `cargo update`) in each touched
  directory.
* **Report**: compiler-style output with exact file/line pointers, planned
  fixes, and advisory-only findings that need a human (deprecated packages
  with no direct successor).

## Supported manifests

| Manifest                               | Ecosystem | Fixes                                                                    |
|----------------------------------------|-----------|--------------------------------------------------------------------------|
| `package.json`                         | npm       | swaps + version bumps, per-version npm `deprecated` flags                |
| `requirements*.txt`, `constraints*.txt`| pypi      | swaps + bumps of pinned/comparator specs                                 |
| `pyproject.toml` (PEP 621 + poetry)    | pypi      | swaps + bumps of pinned/comparator specs                                 |
| `go.mod` (require blocks, indirect)    | go        | module swaps (major-version paths) + version bumps                       |
| `Cargo.toml` (all dependency tables)   | cargo     | swaps + bumps; exact installed version read from `Cargo.lock` when present |

## Install & usage

Requires Python 3.9+ (3.11+ for TOML manifests, or `pip install depthfix[toml]`).
No third-party runtime dependencies otherwise.

```console
$ pip install .            # from a checkout

$ depthfix                 # scan '.', rewrite + install (default mode)
$ depthfix path/           # scan a specific tree
$ depthfix --no-install    # rewrite manifests, skip package managers
$ depthfix --no-fix        # report only, never write
$ depthfix --dry-run       # show planned fixes (same as --no-fix)
$ depthfix --check         # CI mode: report only, exit 1 if findings remain
$ depthfix --offline       # bundled registry only, no network
$ depthfix --refresh       # force refetch of registry data
$ depthfix --registry mine.json   # add/override registry entries
$ depthfix --json          # machine-readable report
```

Exit codes: `0` clean/everything auto-fixable done, `1` findings or manual
attention remain, `2` operational error. `--check` is CI-safe.

## The hybrid registry

Deprecation facts come from two layers:

1. **Bundled static registry** (`depthfix/registries/deprecated.json`) —
   curated entries with a reason, optional successor package (`replacement`),
   alternatives (`suggest`), a minimum supported version (`min_version`, e.g.
   `core-js` 2.x EOL), and EOL dates. Works fully offline.
2. **Live registry APIs** — npm packuments (per-version `deprecated` flag,
   dist-tag `latest`), PyPI JSON API, Go module proxy, crates.io. Fetched
   through `--refresh`-ignorable, 6-hour cached requests; failures degrade
   gracefully to cached/advisory results.

Overrides: pass `--registry file.json` (repeatable, later files win) with
entries that look like the bundled ones. To restore network-layer behavior in
hermetic environments, `DEPTHFIX_CACHE=$dir` relocates the on-disk cache.

## Safety model

The default mode rewrites files and runs installers, so the boundaries are
explicit:

* Runs are deterministic and idempotent — a second run reports nothing new.
* Fixes never touch files when the original text span can't be located
  unambiguously (they stay in the report as "edit manually" advisories).
* Rewrites are span-based: only the package-name / version-spec characters
  between offsets you could point to in a diff viewer are touched.
* `--dry-run` / `--check` / `--no-fix` never write or install.
* Complex/unknown specs are never silently rewritten; they surface as notes.

## Development

```console
$ python3 -m unittest discover -s tests   # 25 tests, stdlib only
$ python3 -m depthfix --dry-run .          # run from a checkout
```

Layout: `depthfix/versioning.py` (dependency-free semver/PEP 440 matcher),
`depthfix/scanners/` (per-manifest parsers → spans), `depthfix/analysis.py`
(registry + upstream → findings & fix plans), `depthfix/fixer.py` (span
splicing), `depthfix/installers.py` (package-manager execution),
`depthfix/registry.py` / `depthfix/registries/deprecated.json` (data),
`depthfix/engine.py`, `depthfix/cli.py`, `depthfix/report.py`.

## Known limitations

* "Outdated" compares the spec floor (or lockfile, for Cargo) with the latest
  upstream release — it does not parse lockfiles for npm/pnpm/yarn yet.
* Unpinned dependencies (no version spec) are reported only when the registry
  itself flags them; there is nothing to rewrite.
* Exotic specifiers (URLs, VCS refs, git branch specs in npm, local paths) are
  skipped rather than guessed at.

## License

MIT.
