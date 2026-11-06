# 0001: Python package and inward dependencies

- Status: Proposed
- Date: 2026-11-06
- Review: technical lead; application engineering

## Context

The new application needs a reliable installation path and boundaries that can
receive society logic without carrying legacy arena or TEE coupling. Source-tree
imports alone can hide packaging mistakes.

## Decision

Use Python >=3.12, the `klados-world` distribution, and `src/klados_world` as the
import package. Build with Hatchling. Separate society domain, application use
cases/ports, inbound API/workers and outbound persistence/integrations. Keep the
root package inert, and dependencies directed inward. Ship a `py.typed` marker.
Foundation implements a UTC Clock port and SystemClock adapter, not engine features.
Use Ruff, strict Pyright, pytest and local pre-commit hooks, with direct tool pins
shared in `pyproject.toml`. Verify installation and a built wheel.

## Alternatives

- A flat source layout: simpler initially, but can accidentally test uninstalled code.
- Copying legacy packages: carries unrelated concerns and is outside this phase.
- Separate distributions per layer: adds release coordination before a demonstrated need.
- Choosing a web framework and task queue now: lacks reviewed application contracts.

## Consequences

A single modular package remains straightforward to develop and distribute. Static
import tests and review protect boundaries. Additional packages and dependencies
need evidence and an ADR where architectural. Direct pins support consistent tools,
but do not solve transitive locking or a full interpreter/OS test matrix. Local
hooks currently assume POSIX `.venv/bin`; portability is documented as follow-up.

See [architecture](../architecture.md) and [platform handoff](../platform-interface.md).
