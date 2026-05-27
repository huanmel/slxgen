# slxgen — Architecture

**Package:** `slxgen`  
**Last updated:** May 2026

---

- [slxgen — Architecture](#slxgen--architecture)
  - [1. Two tool modes](#1-two-tool-modes)
  - [2. Generation pipeline](#2-generation-pipeline)
    - [2.1 Layer overview](#21-layer-overview)
    - [2.2 Layer descriptions](#22-layer-descriptions)
    - [2.3 Pipeline runner and MATLAB session management](#23-pipeline-runner-and-matlab-session-management)
  - [3. Inspection pipeline](#3-inspection-pipeline)
    - [3.1 Pipeline overview](#31-pipeline-overview)
    - [3.2 Output formats](#32-output-formats)
  - [4. Shared coupling](#4-shared-coupling)
  - [5. How the two modes complement each other](#5-how-the-two-modes-complement-each-other)

---

## 1. Two tool modes

`slxgen` has two distinct operating modes that work in opposite directions:

| Mode | Direction | Entry points | Purpose |
| ---- | --------- | ------------ | ------- |
| **Generation** | YAML → Stateflow | `run_pipeline()` (full), `sf_yaml_to_matlab()` (generate only) | Author state machines and build `.slx` models |
| **Inspection** | SLX → text / JSON / PNG | `slx_process()`, `model_to_text()` | Read and document existing Simulink/Stateflow models |

Both modes live in the same package because `slx2txt.py` (Inspection) depends on
`stateflow.py` (Generation) for Stateflow chart parsing. The conceptual boundary is
clear; the package boundary is shared by necessity.

---

## 2. Generation pipeline

### 2.1 Layer overview

```text
YAML file
  │
  ▼ Parser          yaml.safe_load() + yaml_to_sir()              stateflow_sir.py
  │                 Produces SIRModel: typed flat graph of states,
  │                 transitions, variables.
  │
  ▼ Validator       sir_validate()                                 stateflow_sir.py
  │                 9 structural checks on the SIRModel.
  │                 Issues tagged ERROR or WARNING.
  │
  ▼ Normalizer      sir_to_chart_dict()                            stateflow_sir.py
  │                 Converts flat SIRModel → nested chart dict
  │                 (same format the compiler expects).
  │
  ▼ Filter          (planned) opt-in check sets                    stateflow_checks.py
  │                 Restrict features for formal verification,
  │                 safety review, MAAB compliance, etc.
  │
  ├──────────────── Stateflow generator                            stateflow.py
  │                 stateflow_dict_to_matlab()                     [done]
  │                 Emits a self-contained MATLAB .m script.
  │
  ├──────────────── Layout engine                                  elk_layout.py
  │                 ELK bottom-up layout + post-ELK placement.     [done]
  │                 Computes State.Position and transition routing.
  │
  ├──────────────── Mermaid / PlantUML backend                     stateflow_sir.py
  │                 sf_yaml_to_mermaid() → stateDiagram-v2 str.    [Phase 3]
  │
  └──────────────── Formal verification backend                    new
                    SIR → UPPAAL timed automata / NuSMV.           [future]
                    Requires finite_state_only filter profile.

.m script
  │
  ▼ MATLAB Engine   run in connected/started engine session        pipeline.py
  │                 Builds the .slx model.
  │                 Session is shared and left running for reuse.
  │
  ▼ sfLint          slx_lint.m (sfLintChart.m)                     slxgen/matlab/
                    Structural check on the generated .slx.
                    7 checks via Stateflow API.
                    Results written to <model>_lint.json.
```

### 2.2 Layer descriptions

**Parser** — `yaml.safe_load()` reads the YAML file; `yaml_to_sir()` converts the
raw dict into a typed `SIRModel` (flat list of `SIRState`, `SIRTransition`,
`SIRVariable`). All subsequent processing works on `SIRModel`, not raw dicts.

**Validator** — `sir_validate(sir)` runs 9 structural checks covering: duplicate
transition priorities, missing order fields, unresolved state references, missing
initial states, variable initialization, transition action patterns (redundant
override, inconsistent output paths). Returns `list[str]` with ERROR/WARNING tags.
Errors abort generation; warnings are informational.

**Normalizer** — `sir_to_chart_dict(sir)` converts the flat SIRModel back to the
nested chart dict format. This is the SIR→compiler bridge. All planned SIR
extensions (predicate extraction, timer typing) flow through here without touching
the compiler.

**Filter** — (planned) opt-in check sets loaded from `slxgen_config.yaml` or passed
directly as `extra_checks` callables to `sir_validate()`. Core SIR is permissive by
default (everything Stateflow supports is allowed). Filters restrict what is allowed
for specific purposes. Lives in `stateflow_checks.py`, not in the core SIR module.

**Stateflow generator** — `stateflow_dict_to_matlab()` in `stateflow.py`. Converts
the nested chart dict to a MATLAB `.m` script. Frozen: no SIR-specific changes planned
here. The SIR bridge (`sir_to_chart_dict`) handles all future extensions.

**Layout engine** — `elk_layout_bottomup()` in `elk_layout.py` runs ELK (via Node.js
subprocess) in bottom-up per-subchart passes, then converts subchart-relative
coordinates to chart-global. Post-ELK sink-state repositioning and arc routing are
applied in `stateflow.py`. Entirely independent of SIR.

**MATLAB execution** — runs the generated `.m` script inside a MATLAB Engine session.
Before each run: workspace is cleared (`clear`) and any previously loaded version of
the model is closed (`bdclose`), preventing stale-state conflicts across iterations.

**sfLint** — `slx_lint.m` calls `sfLintChart.m` on every Stateflow chart in the
`.slx` using the Stateflow API. 7 structural checks: `NoDefaultTransition`,
`MultipleDefaultTrans`, `UnreachableState`, `MissingDestination`,
`DuplicateExecutionOrder`, `DuplicateStateName`, `TransitionScopeMismatch`.
Results are deduplicated and written to `<model>_lint.json` in the output directory.

**Layer assignment guide** — assign any bug or feature to exactly one layer:

| Symptom | Layer |
|---------|-------|
| Overlapping transition labels | Layout engine |
| State box sized too small | Layout engine |
| Missing `initial_value` warning | Validator |
| Enum type not accepted | Parser + Validator |
| Verification rule (AND states forbidden) | Filter / stateflow_checks.py |
| New diagram output format | Alt backend |
| sfLint false positive on AND state | sfLint (sfLintChart.m) |

---

### 2.3 Pipeline runner and MATLAB session management

`run_pipeline()` in `pipeline.py` wraps all four steps (validate → generate →
MATLAB run → sfLint) into a single call:

```python
from slxgen import run_pipeline

result = run_pipeline(
    'my_chart.yaml',
    model_name='MyCtrl',
    run_matlab=True,        # False = write .m only, no MATLAB needed
    session_name='slxgen',  # shared session name
    open_desktop=False,     # True = open full MATLAB GUI when starting new engine
    lint=True,
)
# result: {'script': Path, 'slx': Path, 'issues': list[str], 'lint': list[dict]}
```

**MATLAB session management:**

`run_pipeline` connects to an existing shared MATLAB session if one is found, or
starts a new engine and shares it under `session_name`. The engine is left running
after the call so the next invocation connects instantly instead of paying the
cold-start cost (~15–30 s).

Sessions started from Python (`matlab.engine.start_matlab()`) close when the Python
process exits. For a persistent session that survives Python restarts:

```matlab
% Run once in the MATLAB Command Window:
matlab.engine.shareEngine('slxgen')
```

After that, every `run_pipeline(..., run_matlab=True)` call reconnects in <1 s.
Use `open_desktop=True` when starting a new engine from Python — this opens the full
MATLAB desktop so you can inspect the workspace and command window during execution.

**Before each MATLAB run**, `run_pipeline` automatically:

1. Runs `clear` — removes workspace variables from previous iterations
2. Runs `bdclose('<model_name>')` if loaded — avoids `new_system` conflicts

This prevents stale state from one run leaking into the next without requiring a
full engine restart.

---

## 3. Inspection pipeline

### 3.1 Pipeline overview

```text
SLX file  (ZIP of XML files)
  │
  ▼ parse_slx()                                              slx2txt.py
  │   Unzips the SLX. Reads blockdiagram.xml and per-system
  │   XML files. Calls parse_stateflow_chart() (stateflow.py)
  │   for any embedded Stateflow chart blocks.
  │   Returns a raw model dict: blocks + connections.
  │
  ▼ enrich_connections()                                     slx2txt.py
  │   Resolves SID references → human-readable port names.
  │   Attaches incoming/outgoing connection info to each block.
  │
  ▼ filter_model_data()                                      slx2txt.py
  │   Scope the model to specific subsystems, block types,
  │   or connection patterns. Optional.
  │
  ▼ Outputs (any combination)
      model_to_text()        plain-text block/connection report
      model_to_markdown()    Mermaid block diagram (Simulink topology)
      slx_process()          save JSON + text + markdown to output dir
      compare_models()       structural diff between two parsed models
      process_model_tree()   recursive traversal following library references
```

### 3.2 Output formats

| Function | Output | Use case |
| -------- | ------ | -------- |
| `model_to_text()` | Plain text | Quick inspection, LLM input |
| `model_to_markdown()` | Mermaid block diagram | Documenting Simulink topology |
| `slx_process()` | JSON + text + markdown saved to disk | Batch processing, archiving |
| `compare_models()` | Diff dict | Regression checking between model versions |
| `process_model_tree()` | Recursive slx_process for all referenced libraries | Full model documentation |

Screenshots / PNG export: `slx_process(save=True)` triggers a MATLAB script that
opens the model and exports a PNG. Requires MATLAB on the path.

---

## 4. Shared coupling

`slx2txt.py` imports from `stateflow.py`:

```python
from .stateflow import (
    load_stateflow_machine,
    parse_stateflow_chart,
    stateflow_chart_to_dict,
    _collect_sf_charts,
    ...
)
```

`parse_stateflow_chart()` extracts Stateflow chart data from the SLX ZIP. This
coupling is intentional — the Stateflow XML parser lives in `stateflow.py` because
it was built alongside the generator and shares the chart dict format. Splitting the
package would require duplicating or re-exporting that parser.

Both modes are exported from `slxgen/__init__.py`. The public API makes no
distinction — callers import from `slxgen` regardless of which mode they use.

---

## 5. How the two modes complement each other

```text
Author YAML spec
  → run_pipeline(..., run_matlab=False)    validate + write .m (no MATLAB needed)
  → run_pipeline(..., run_matlab=True)     build .slx + sfLint in one call
  → slx_process()                          inspect the generated model
  → compare_models()                       verify structure matches the YAML intent

Legacy model workflow:
  existing .slx
  → slx_process()             extract text/JSON representation
  → stateflow_chart_to_dict() extract chart dict from Stateflow charts
  → use as authoring input    clean up and feed into slxgen YAML
```

**Auto-screenshot on generation** — the `.m` script that `sf_yaml_to_matlab()` emits
can include a `print()` call to export a PNG before closing the model. Embedding this
in the generated script means every generation run produces a screenshot automatically,
with no separate step. The PNG can be:

- Opened by a human for fast visual sanity-check
- Sent to a multimodal LLM for automated audit ("does the chart topology match the
  YAML spec?", "are all states reachable?", "does the layout look reasonable?")
- Archived alongside the `.m` script for traceability

This is cheaper than running the full Inspection pipeline and useful earlier in the
loop — before you have a baseline to compare against.

Concrete use cases:

- **Quick visual audit**: generate → auto-screenshot → human or LLM reviews the PNG
  without opening MATLAB.
- **CI regression**: generate `.m` → run MATLAB → parse `.slx` → `compare_models()`
  against a saved baseline to detect unintended structural changes.
- **Reverse engineering**: `slx_process()` a legacy model to get a human-readable
  summary; use the chart dict as a starting point for a YAML spec.
- **Documentation**: `model_to_markdown()` produces a Mermaid diagram of the Simulink
  block topology for inclusion in design documents.
- **Pre-MATLAB review**: `sf_yaml_to_mermaid()` (Phase 3) will produce a state diagram
  directly from YAML for review before any MATLAB is generated.
