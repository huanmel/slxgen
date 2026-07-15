# slxgen

Specs-driven Simulink model generator for MATLAB/Simulink.

Generate `.slx` models from a compact YAML specification — no manual GUI work required.
Two block types are supported:

- **Stateflow charts** (`type:` absent or `stateflow`) — state machines with states,
  transitions, actions, junctions, and calibration parameters.
- **MATLAB Function blocks** (`type: matlab_function`) — algorithmic blocks with typed
  inputs/outputs, a code body, and workspace parameters.

## User guides

- [Workflow guide](workflow.md) — author YAML specs, iterate with PlantUML, run the pipeline
  - [§1.3 — PlantUML-first prototyping](workflow.md#13-plantuml-first-prototyping)
  - [§1.4 — Enum type definitions](workflow.md#14-enum-type-definitions)
  - [§1.8 — MATLAB Function blocks](workflow.md#18-matlab-function-blocks)
  - [§4.1 — Quick-start snippet](workflow.md#41-quick-start)
- [Stateflow design guidelines](stateflow_model_creation_guideline.md) — ISO 26262 / MAAB-compliant chart authoring

## Developer reference

- [Architecture](architecture.md) — package structure, module responsibilities, data-flow overview
- [Pipeline algorithm reference](algorithms.md) — step-by-step walkthrough with code-level detail
- [Project roadmap](roadmap.md) — current state, planned work items, documentation improvements

## Quick links

- [GitHub repository](https://github.com/huanmel/slxgen)
- [Example: fan controller YAML](https://github.com/huanmel/slxgen/blob/main/example/model_gen/fan_ctrl_sf.yaml)
- [Example: generated SLDD init script](https://github.com/huanmel/slxgen/blob/main/example/model_gen/generated/sldd_gen/fan_ctrl_sf_sldd.m)
