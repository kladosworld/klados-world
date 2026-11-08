# Reproducible application inputs

The application runtime uses `requirements-runtime.lock`; build tooling and test tooling use separate hash-verified locks. The locks were compiled by uv 0.13.7 with universal Python 3.12+ resolution. Every Docker install uses `--require-hashes` and binary distributions. The application wheel is built using locked Hatchling without isolated dependency downloads, then installed without dependency resolution. Package version ranges in project metadata remain compatibility declarations, not the deployed dependency selection.

`build-inputs.json` records the exact Python base index digest, Dockerfile frontend digest, lock generator, platform, and normalized-LF input hashes. Both build stages use the same pinned Python 3.12 Bookworm base. Python 3.12 and 3.13 CI resolve each lock twice, comparing package versions and downloaded distribution hashes. Python 3.12 CI performs two uncached runtime builds, compares installed packages, and runs the complete application suite inside the locked test image against its disposable PostgreSQL database. No registry push or deployment occurs.

To regenerate deliberately, install `uv==0.13.7`, then run:

```sh
uv pip compile pyproject.toml --universal --python-version 3.12 --generate-hashes --no-header --no-annotate -o requirements-runtime.lock
uv pip compile requirements-build.in --universal --python-version 3.12 --generate-hashes --no-header --no-annotate -o requirements-build.lock
uv pip compile pyproject.toml requirements-build.in --extra dev --universal --python-version 3.12 --generate-hashes --no-header --no-annotate -o requirements-dev.lock
```

Review dependency changes and regenerate manifest hashes from UTF-8 text normalized to LF. Run all application and image checks before accepting updated inputs. An index compromise or unavailable historical wheel remains a failure, not permission to bypass hashes.

## Interface for deployment engineering

The existing `deployment/local/Dockerfile`, `runtime` and `tests` targets, build context allowlist, optional `proxy_ca` BuildKit secret, port 8000, numeric user 10001:10001, entrypoint, control-file path and data paths are retained. No system packages, Docker daemon configuration, Compose, deployment scripts or workflows are changed. The build requires BuildKit as before.

Use a clean standalone clone at the explicitly approved application commit. Existing operations tooling expects `.git` to be a directory; a development worktree is not an installation source. Pass the exact approved commit to the existing image builder and retain its OCI revision label. Existing cached images should only be reused when their revision label matches.

CI emits `PILOT_IMAGE_PROVENANCE` with source revision, base digest, two image IDs, runtime lock hash and installed-package fingerprint. Hash-locked resolution does not promise identical image IDs across builders. Record the actual staging image ID/digest and promote that exact validated image instead of rebuilding it or relying on a mutable tag. CI images are validation evidence, not published pilot artifacts.

Shared Purpose remains off unless the worker process explicitly receives `KLADOS_SHARED_PURPOSE=building-a-society-v1`. Application support does not activate or forward this setting through existing operations configuration. Deployment engineering must review that interface separately; web and observer credentials, budgets and stop controls are unchanged.
