# slxgen

Specs-driven Stateflow model generator for MATLAB/Simulink.

Generate `.slx` models, PlantUML diagrams, and MATLAB enum artefacts from
a compact YAML specification — no manual Stateflow GUI work required.

## User guides

- [Workflow guide](workflow.md) — author YAML specs, iterate with PlantUML, run the pipeline
  - [§1.3 — PlantUML-first prototyping](workflow.md#13-plantuml-first-prototyping)
  - [§1.4 — Enum type definitions](workflow.md#14-enum-type-definitions)
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
