# slxgen — Project Roadmap

**Last updated:** May 2026- [slxgen — Project Roadmap](#slxgen--project-roadmap)

- [slxgen — Project Roadmap](#slxgen--project-roadmap)
  - [Current state](#current-state)
  - [Project layer architecture](#project-layer-architecture)
  - [Strategic direction](#strategic-direction)
  - [Work items](#work-items)
    - [Priority 1 — Mermaid backend (Phase 3) \[~50–80 LOC\]](#priority-1--mermaid-backend-phase-3-5080-loc)
    - [Priority 2 — Configurable validator](#priority-2--configurable-validator)
    - [Priority 3 — Extensible verification plugin system](#priority-3--extensible-verification-plugin-system)
    - [Priority 4 — YAML normalizer (Phase 4)](#priority-4--yaml-normalizer-phase-4)
    - [Priority 5 — Layout linter + layout engine improvements](#priority-5--layout-linter--layout-engine-improvements)
    - [Priority 5b — Simulink model linter](#priority-5b--simulink-model-linter)
    - [Priority 5c — Wrap generated .m scripts as MATLAB functions](#priority-5c--wrap-generated-m-scripts-as-matlab-functions)
    - [Priority 6 — YAML schema gaps (as needed)](#priority-6--yaml-schema-gaps-as-needed)
    - [Priority 7 — Predicate/timer extraction (Phase 5)](#priority-7--predicatetimer-extraction-phase-5)
    - [Priority 8 — Formal verification backend (long-term)](#priority-8--formal-verification-backend-long-term)
  - [Decisions and constraints](#decisions-and-constraints)


---

## Current state

Phases 1 and 2 of the SIR are complete. The live generation pipeline:

```text
YAML  →  yaml_to_sir()  →  sir_validate()  →  sir_to_chart_dict()  →  stateflow_dict_to_matlab()
```

SIR validates structure (9 checks across state defaults, transition priorities,
variable initialization, and transition action patterns), drives generation via
`sir_to_chart_dict()`, and exports JSON for inspection. The MATLAB codegen and
ELK layout engine are unchanged.

---

## Project layer architecture

All work is assigned to one of these layers. Keeps scope clear.

| Layer | Current implementation | File |
| ----- | ---------------------- | ---- |
| **Parser** | `yaml.safe_load()` + `yaml_to_sir()` | `stateflow_sir.py` |
| **Validator** | `sir_validate()` — 9 structural checks | `stateflow_sir.py` |
| **Normalizer** | `sir_to_chart_dict()` — flat SIR → nested dict | `stateflow_sir.py` |
| **Config / filter** | (planned) `ValidationConfig` + plugin check sets | `stateflow_sir.py`, new `stateflow_checks.py` |
| **Compiler / SF generator** | `stateflow_dict_to_matlab()` — frozen | `stateflow.py` |
| **Layout engine** | ELK + post-ELK placement | `elk_layout.py`, `stateflow.py` |
| **Alt backends** | (planned) Mermaid/PlantUML, formal tools | new |

---

## Strategic direction

SIR is a **constrained execution language**, not a mirror of all Stateflow.
By default it supports everything Stateflow supports. Opt-in check sets restrict
what is allowed for specific purposes (formal verification, safety review).

```text
          ┌─── Stateflow generator    (done)
YAML ─► SIR ─── Mermaid / PlantUML   (next)
          ├─── YAML normalizer        (planned)
          └─── Formal verification    (future, gated by profile)
```

One YAML definition → multiple consistent outputs. Avoids maintaining a simplified
formal model separately from the production Stateflow model.

---

## Work items

### Priority 1 — Mermaid backend (Phase 3) [~50–80 LOC]

Generate a Mermaid `stateDiagram-v2` diagram directly from `SIRModel`.

- Proves multi-backend with zero regression risk to MATLAB path
- GitHub renders Mermaid natively — no infrastructure needed
- Entry point: `sf_yaml_to_mermaid(yaml_path) -> str` in `stateflow_sir.py`
- Nested subchart states → `state X { }` blocks
- Fault-role states → `<<fault>>` comment annotation

PlantUML can follow if richer diagram output is needed (better nested state support,
notes, colors). Architecture is the same; only the emit function differs.

### Priority 2 — Configurable validator

Two motivations:

**a) Enum variables**: The validator warns when an output/local has no `initial_value`.
Enum-typed variables often cannot have a YAML-level default — the enum definition
lives in the SLDD (Simulink Data Dictionary). The tool is not for data modeling;
type mismatches are caught at MATLAB compile time. The warning should be suppressible.

**b) All checks should be configurable**: Teams should be able to enable/disable
checks and override severity per rule.

Design — optional `slxgen_config.yaml` in the project directory.
The same file carries both validator and layout sections:

```yaml
validator:
  initial_value_primitives_only: true    # warn only for boolean/uint8/int32/single/double
  initial_value_required: false          # suppress all initial_value warnings
  duplicate_priority: error              # ERROR | WARNING | off
  missing_order: warning
  redundant_transition_action: warning
  inconsistent_output_paths: warning

layout:
  leaf_width: 150           # _SF_LEAF_W  — fixed width of leaf (childless) states, px
  header_height: 22         # _SF_HEADER_H — title-bar overhead, px
  line_height: 16           # _SF_LINE_H  — pixels per label line (en/du/ex actions)
  node_spacing: 30          # ELK elk.spacing.nodeNode, px
  default_transition_offset: 20   # dot placed this many px above destination top
  direction: DOWN           # main layout axis: DOWN | RIGHT | UP | LEFT
  edge_routing: SPLINES     # ELK edgeRouting: SPLINES | ORTHOGONAL | POLYLINE
```

The `layout:` section exposes the named constants in `stateflow.py` and
`elk_layout.py` without requiring code changes per model. ELK options that are
already passable via `elk_options` dict in `run_pipeline()` are preserved as-is;
the config file is an alternative authoring surface for the same knobs.

`sir_validate(sir, config=None)` — config is optional; defaults match current behavior.

`run_pipeline()` and `sf_yaml_to_matlab()` would auto-discover `slxgen_config.yaml`
in the YAML file's directory (or accept an explicit `config_path` parameter).

### Priority 3 — Extensible verification plugin system

Verification-specific checks belong in a separate module, not in `stateflow_sir.py`.
Keeps core SIR lean; check sets can be extended without modifying core code.

Planned file layout:

```text
slxgen/
  stateflow_sir.py          ← core SIR + structural checks (always run)
  stateflow_checks.py       ← built-in optional check sets
  # future: stateflow_iso26262.py, stateflow_maab.py, ...
```

`sir_validate()` gains an `extra_checks` parameter:

```python
sir_validate(sir, config=None, extra_checks=None)
# extra_checks: list[Callable[[SIRModel], list[str]]]
```

The verification profile becomes a check set in `stateflow_checks.py`:

```python
def verification_profile_checks(profile: dict) -> Callable:
    def _check(sir: SIRModel) -> list[str]: ...
    return _check
```

Used as:

```python
from slxgen.stateflow_checks import verification_profile_checks
issues = sir_validate(sir, extra_checks=[verification_profile_checks(profile)])
```

Feature classification for a `finite_state_only` profile:

| Feature            | Default | finite_state_only |
| ------------------ | ------- | ----------------- |
| OR states          | Full    | Full |
| Hierarchy          | Full    | Full |
| Bounded variables  | Full    | Required |
| AND states         | Full    | ERROR |
| History junction   | Full    | ERROR |
| Native timers      | Full    | ERROR |
| MATLAB functions   | Full    | WARNING |
| Events / send()    | Full    | ERROR |

### Priority 4 — YAML normalizer (Phase 4)

`sir_normalize_yaml(yaml_path, output_path)`:

- Fill in missing `order` fields based on list position
- Sort transitions by source state then priority
- Add `initial_value: ~` stubs for output/local variables flagged by validator
- Emit clean YAML with consistent key order

Supports the **edit-validate loop** — the intended authoring workflow:

```text
write YAML  →  validate  →  fix issues  →  validate  →  ...  →  generate
```

The normalizer automates safe fixes so authors spend fewer iterations.
A `--strict` flag (abort on any WARNING) enforces a clean spec before generation.

### Priority 5 — Layout linter + layout engine improvements

**Layout linter** — `elk_validate(positions, chart_dict, elk_options) -> list[str]`
in `elk_layout.py`. Runs post-ELK, pre-MATLAB. No MATLAB or `.slx` required.
Returns `WARNING`/`ERROR` strings in the same format as `sir_validate()`.

Checks:

- **Fan-in label overlap**: multiple transitions targeting the same state with
  `MidPoint.y` values within ±20 px — warn with state name and transition indices.
- **Undersized state box**: estimated text height (action line count × line height)
  exceeds ELK-assigned box height — warn with suggested `height:` override.
- **Label truncation**: transition label was substituted for ELK sizing (long label);
  actual rendered label may exceed the visual boundary.

Layout fixes (same work, fix side):

- **Stagger fan-in labels**: detect transitions with same tier → same target; offset
  `MidPoint.y` by ±20 px per transition index. ~20 LOC in `stateflow.py`.
- **State box sizing**: count wrapped lines at ~40 chars/line and size height
  accordingly, instead of current character-count heuristic.
- **Back-edge routing via junctions**: high effort — defer.

### Priority 5b — Simulink model linter

Post-generation structural check on the `.slx`. Two complementary approaches that
can be used independently or together:

**Approach A — Python (`parse_slx`)**: no MATLAB required; works on `.slx` ZIP XML.
Fast, suitable for CI without a MATLAB license.

Entry point: `slx_lint(slx_path) -> list[str]` in a new `slxgen/slx_lint.py`.

Checks:

- **Unconnected ports**: Inport/Outport blocks with no signal line.
- **State overlap**: two states with `Position` bounding boxes that intersect
  (Stateflow silently accepts this — it is a layout error).
- **Orphaned transitions**: transition source/target position falls outside every
  known state bounding box (reparenting error — see `slxgen_internals.md` §3.5).
- **Transition label overflow**: estimated label width exceeds state width at the
  attachment point.
- **Chart naming**: chart name matches the block name and design guideline conventions.

**Approach B — Native MATLAB script (`slx_lint.m`)**: runs inside MATLAB with full
Simulink and Stateflow API access. Deeper semantic checks not reachable from Python.

The generated script opens the model, runs checks via MATLAB APIs, prints issues
in the same `WARNING:` / `ERROR:` format, then closes the model without saving.
Can be triggered from Python after generation or run standalone in MATLAB.

Checks only available via MATLAB:

- **Signal dimension / type mismatches**: Simulink propagation errors caught before
  simulation.
- **Stateflow compilation errors**: `sfbuild` or `Stateflow.Chart.verify()` — catches
  invalid action code, unreachable states, and chart configuration issues.
- **Model Advisor rules**: selected MAAB or ISO 26262 checks via
  `Simulink.ModelAdvisor`.
- **Uninitialized data**: Simulink diagnostics for undriven signals.

Usage:

```python
# Python linter — no MATLAB
from slxgen.slx_lint import slx_lint
for msg in slx_lint('out.slx'): print(msg)

# MATLAB linter — called from Python after generation
import subprocess
subprocess.run(['matlab', '-batch', f"slx_lint('out.slx')"])

# Or run slx_lint.m directly in MATLAB
# >> slx_lint('out.slx')
```

File to create: `slxgen/slx_lint.m` — standalone MATLAB script, no toolbox
beyond Simulink/Stateflow required.

### Priority 5c — Wrap generated .m scripts as MATLAB functions

Generated scripts currently run in the MATLAB base workspace.  Repeated
invocations in the same session can accumulate stale variables and trigger
`new_system` conflicts if a previous model was not fully closed.

**Workaround in place:** `run_pipeline()` runs `clear` and
`bdclose('<model>')` before each execution.  This covers the common case
but is not airtight (e.g. global variables, persistent data).

**Proper fix:** emit the generated `.m` as a function instead of a script.

```matlab
% current (script)
mdl = 'DevCtrl_StMach';
new_system(mdl); ...

% target (function)
function DevCtrl_StMach()
    mdl = 'DevCtrl_StMach';
    new_system(mdl); ...
end
```

All locals become function-scoped; no workspace pollution between runs.

Implementation:

- `stateflow.py` codegen: prepend `function <model_name>()\n` and append
  `\nend` around the emitted body.
- `pipeline.py` step 3: replace `eng.run(script_path)` with
  `eng.addpath(out_dir); eng.eval(f'{model_name}()')`.
- The `export_charts` helper call inside the script stays unchanged.

Risk: low — the generated body is self-contained; only the outer wrapper changes.

### Priority 6 — YAML schema gaps (as needed)

- **Variable size/dimensions** ✓ implemented: `size:` field for inputs, outputs,
  and locals. Default is scalar — omitting `size:` or writing `size: [1]` both
  produce a scalar signal and no `Props.Array.Size` property is emitted (Stateflow
  default). To disable the feature for a variable, simply omit the field.

  ```yaml
  inputs:
    - {name: vec_in,  type: single, size: [3, 1]}   # 3×1 column vector → Props.Array.Size = '[3 1]'
    - {name: mat_in,  type: double, size: [3, 3]}   # 3×3 matrix        → Props.Array.Size = '[3 3]'
    - {name: inh_in,  type: single, size: [-1]}     # inherited          → Props.Array.Size = '[-1]'
    - {name: flag,    type: boolean}                 # scalar — size: omitted, no Props.Array.Size emitted
  locals:
    - {name: x,       type: uint8,  size: [1]}      # explicit scalar — same as omitting size:
  ```

  | `size:` value | Meaning | `Props.Array.Size` emitted |
  | --- | --- | --- |
  | omitted | scalar (default) | no — Stateflow default |
  | `[1]` | explicit scalar | no — same as omitting |
  | `[n, m]` | n×m array | yes — `'[n m]'` |
  | `[-1]` | inherited from connected signal | yes — `'[-1]'` |

  SIR: `SIRVariable.size` — always set; `[1]` when unspecified (configurable).
  Codegen emits `Props.Array.Size` only when size ≠ `[1]`.

  **Configuring the default** — pass `default_size` to any entry point to change
  what unspecified variables receive:

  ```python
  # All variables without size: become inherited
  run_pipeline('my.yaml', default_size=[-1])
  sf_yaml_to_matlab('my.yaml', 'out.m', default_size=[-1])
  yaml_to_sir(chart_dict, default_size=[-1])
  ```

  Per-variable overrides still work: an explicit `size:` in the YAML always takes
  precedence over `default_size`.

- **Junctions**: decision nodes for shared transition routing — YAML key + codegen.
- **Inner transitions**: `inner: true` — stays within source's active child.
- **Named events**: `Stateflow.Event` — no YAML key yet.

### Priority 7 — Predicate/timer extraction (Phase 5)

Parse `count(x)>t`, `duration(x)>t`, `after(n,tick)` into typed `SIRPredicate` /
`SIRTimer` nodes. Required before a formal verification backend can emit meaningful
output. `sir_to_chart_dict()` translates them back to inline strings for the existing
codegen — no regression risk.

### Priority 8 — Formal verification backend (long-term)

SIR → UPPAAL timed automata XML, or NuSMV transition system.
Only viable for models that pass `finite_state_only` profile.
UPPAAL is the closest semantic match to Stateflow's timing model.
Requires Priority 3 (plugin system) and Priority 7 (predicate extraction) first.

---

## Decisions and constraints

- **`sir_to_matlab()` is not planned.** The `sir_to_chart_dict()` bridge handles all
  current and planned SIR extensions. The frozen codegen is an asset, not a liability.

- **YAML stays as the authoring format.** The SIR is a processing layer, not an
  authoring format. The JSON export is an inspection artifact only.

- **No enum validation in the SIR.** Enum definitions live in the SLDD. Type
  mismatches are caught at MATLAB compile time. slxgen is not a data modeling tool.

- **Verification requires an explicit profile.** Unrestricted Stateflow is not
  verifiable (hierarchy explosion, MATLAB code opacity). The profile gate ensures
  authors opt in with awareness of what's restricted.
