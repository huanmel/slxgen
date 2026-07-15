# slxgen — Claude Code Project Instructions

## What this project is

`slxgen` generates MATLAB/Simulink blocks from YAML specifications.
Two block types are supported:

- **Stateflow charts** (`type:` absent): `YAML → yaml_to_sir() → sir_validate() → sir_to_chart_dict() → stateflow_dict_to_matlab()`
- **MATLAB Function blocks** (`type: matlab_function`): `YAML → yaml_to_mlf() → mlf_validate() → mlf_to_matlab()`

Both directions are supported: YAML → `.slx` (generation) and `.slx` → text/JSON/PNG (inspection).

## Environment

```
Python: C:\Users\ivanm\miniforge3\envs\py311_slxgen\python.exe
Conda env: py311_slxgen   (required for matlab.engine)
Package root: slxgen/
```

Always use the `py311_slxgen` env — the system `python` alias does not have `matlab.engine`.

## Key files

| File | Role |
| ---- | ---- |
| `slxgen/stateflow_sir.py` | SIR dataclasses, `yaml_to_sir()`, `sir_validate()`, `sir_to_chart_dict()`, diagram export |
| `slxgen/stateflow.py` | MATLAB codegen: `stateflow_dict_to_matlab()`, ELK layout integration |
| `slxgen/matlab_function.py` | MATLAB Function block codegen: `yaml_to_mlf()`, `mlf_validate()`, `mlf_to_matlab()` |
| `slxgen/elk_layout.py` | ELK-based compound-state layout engine |
| `slxgen/enum_gen.py` | Enum classdef + SLDD script generation |
| `slxgen/matlab/sfLintChart.m` | Structural lint checker for built Stateflow charts |
| `example/model_gen/fan_ctrl_sf.yaml` | Canonical Stateflow example — params, desc, req, enums, subchart, history, junctions |
| `example/model_gen/simple_filter_mlf.yaml` | Canonical MATLAB Function block example — inputs, outputs, params, code body |
| `example/model_gen/Ex1_StMach.yaml` | Simpler Stateflow reference example |

## Before writing YAML

Read **`docs/workflow.md §1.2`** — it contains the complete YAML schema summary (paste it into your prompt context).

Section map for specific features:

| Topic | Section |
| ----- | ------- |
| Full YAML schema (Stateflow) | `docs/workflow.md §1.2` |
| PlantUML-first prototyping | `docs/workflow.md §1.3` |
| Enum type definitions | `docs/workflow.md §1.4` |
| Connective junctions (`junction: true`) | `docs/workflow.md §1.5` |
| Descriptions and requirements (`desc:`, `req:`) | `docs/workflow.md §1.6` |
| Parameters / calibration data (`params:`) | `docs/workflow.md §1.7` |
| MATLAB Function blocks (`type: matlab_function`) | `docs/workflow.md §1.8` |
| Design rules (naming, hierarchy, levels 0–3) | `docs/stateflow_model_creation_guideline.md` |

## Before modifying the pipeline

Read **`docs/architecture.md`** and **`docs/sir_notes.md`**.

**Critical pattern**: any new field on `SIRState` or `SIRTransition` MUST be explicitly
passed through `sir_to_chart_dict()` or it is silently dropped before codegen.

## Running the pipeline (generation — YAML → SLX)

```python
# Quick — full pipeline in one call
from slxgen import run_pipeline
run_pipeline('my_chart.yaml', run_matlab=True, session_name='slxgen')
# subsys_ref=True → also produces <stem>_sub.slx (Subsystem Reference component for reuse)

# Explicit — validate first, then generate
from slxgen.stateflow_sir import yaml_to_sir, sir_validate
from slxgen.stateflow import sf_yaml_to_matlab
```

See `example/model_gen/quick_start.py` for a 15-line working example.

## Inspecting an existing model (SLX → text / YAML)

To export a model to text for LLM review:

```python
from slxgen import slx_process, sf_yaml_to_puml

# Extract report + Stateflow YAML from an existing .slx
slx_process('MyCtrl.slx', filters={}, save=True, output_dir='review/',
            outputs=['report.txt', 'arch.md', 'sf.yaml'])

# Convert extracted Stateflow YAML to PlantUML for visual review
sf_yaml_to_puml('review/MyChart_sf.yaml', output_path='review/MyChart_sf.puml')
```

Key output formats:

| File | Use for |
| ---- | ------- |
| `_report.txt` | Paste to LLM — concise block/connection summary |
| `_arch.md` | Mermaid topology diagram |
| `_sf.yaml` | Stateflow chart in slxgen YAML — feed back for modification |

Full details: `docs/workflow.md §4.5`

## Private data warning

`work/dp187_HMI/` contains **private project data** and must NOT be used as source
material for examples, documentation, or generated outputs that leave this repository.

## Documentation map

Full doc index: `docs/index.md` (also at GitHub Pages).

| Document | Purpose |
| -------- | ------- |
| `docs/workflow.md` | YAML schema, edit-validate-generate loop, tool commands |
| `docs/architecture.md` | Pipeline layers, entry points |
| `docs/algorithms.md` | Step-by-step pipeline walkthrough |
| `docs/roadmap.md` | Planned features and priorities |
| `docs/stateflow_model_creation_guideline.md` | Design rules for Level 0–3 models |
| `docs/slxgen_internals.md` | ELK layout, coordinate rules |
| `docs/sir_notes.md` | SIR design rationale and schema reference |
| `docs/issues.md` | Known bugs, workarounds |
| `DEVNOTES.md` | Developer notes — architecture decisions, quirks |
