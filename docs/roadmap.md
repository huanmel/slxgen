# slxgen — Project Roadmap

**Last updated:** June 2026

- [slxgen — Project Roadmap](#slxgen--project-roadmap)
  - [Current state](#current-state)
  - [Project layer architecture](#project-layer-architecture)
  - [Strategic direction](#strategic-direction)
  - [Work items](#work-items)
    - [Priority 1 — Mermaid + PlantUML export (Phase 3) ✓ done](#priority-1--mermaid--plantuml-export-phase-3--done)
    - [Priority 1b — PlantUML export: history junction](#priority-1b--plantuml-export-history-junction)
    - [Priority 1c — PlantUML ↔ YAML roundtrip (import)](#priority-1c--plantuml--yaml-roundtrip-import)
    - [Priority 2 — Configurable validator](#priority-2--configurable-validator)
    - [Priority 3 — Extensible verification plugin system](#priority-3--extensible-verification-plugin-system)
    - [Priority 4 — YAML normalizer (Phase 4)](#priority-4--yaml-normalizer-phase-4)
    - [Priority 5 — Layout linter + layout engine improvements](#priority-5--layout-linter--layout-engine-improvements)
    - [Priority 5b — Simulink model linter](#priority-5b--simulink-model-linter)
    - [Priority 5c — Wrap generated .m scripts as MATLAB functions](#priority-5c--wrap-generated-m-scripts-as-matlab-functions)
    - [Priority 5d — Pure-Python layout engine (remove Node.js dependency)](#priority-5d--pure-python-layout-engine-remove-nodejs-dependency)
    - [Priority 6 — YAML schema gaps (as needed)](#priority-6--yaml-schema-gaps-as-needed)
    - [Priority 6b — Parameter / calibration data ✓ done](#priority-6b--parameter--calibration-data--done)
    - [Priority 6c — Graphical functions in Stateflow charts](#priority-6c--graphical-functions-in-stateflow-charts)
    - [Priority 6d — Requirements linking (reqID on entities)](#priority-6d--requirements-linking-reqid-on-entities)
    - [Priority 6e — Description and comment fields](#priority-6e--description-and-comment-fields)
    - [Priority 7 — Predicate/timer extraction (Phase 5)](#priority-7--predicatetimer-extraction-phase-5)
    - [Priority 8 — Formal verification backend (long-term)](#priority-8--formal-verification-backend-long-term)
  - [Decisions and constraints](#decisions-and-constraints)


---

## Current state

Phases 1–3 of the SIR are complete. The live generation pipeline:

```text
YAML  →  yaml_to_sir()  →  sir_validate()  →  sir_to_chart_dict()  →  stateflow_dict_to_matlab()
```

SIR validates structure (9 checks across state defaults, transition priorities,
variable initialization, and transition action patterns), drives generation via
`sir_to_chart_dict()`, and exports JSON for inspection. The MATLAB codegen and
ELK layout engine are unchanged.

**Diagram export (Phase 3 — done):** `sir_to_mermaid()` / `sf_yaml_to_mermaid()` and
`sir_to_puml()` / `sf_yaml_to_puml()` both live in `stateflow_sir.py`. PlantUML is
the recommended visual preview format (encodes `en`/`du`/`ex` actions); Mermaid is the
lightweight structure-only alternative. See `docs/workflow.md §4.4`.

**Enum codegen (done):** `enum_gen.py` generates MATLAB artefacts from `enums:` /
`data_file:` definitions in the model YAML.  Two outputs: `<TypeName>.m` classdef files
(simulation-ready, no MATLAB required to generate) and a `sldd_gen/<stem>_sldd.m` script
that creates a Simulink Data Dictionary for production use.  Both are wired into
`run_pipeline` via `gen_enums=True` (default) and `gen_sldd=False` (opt-in).
Variable types use `"Enum: TypeName"` syntax; the generator emits
`Props.Type.Method = 'Enumerated'` automatically. See `docs/workflow.md §1.4`.

**Connective junctions (done):** `junction: true` on any state in the YAML makes it a
connective junction — a circle routing node with no actions.  Outgoing transitions use
`order:` priorities; the last is the else branch.  `sir_to_puml()` renders junctions as
`<<choice>>`; the MATLAB codegen emits `Stateflow.Junction` with `Position.Center` /
`Position.Radius`.  See `docs/workflow.md §1.5` and
`example/model_gen/junction_test_sf.yaml`.

**Parameters (done):** `params:` section in YAML — `Stateflow.Data` with `Scope='Parameter'`.
Supports `value:` (inline constant → `Props.InitialValue`) or no value (workspace variable
pattern, `Props.InitialValue` left empty).  Supports `type:`, `size:` identical to
`inputs`/`locals`.  See `docs/workflow.md §1.2` and `example/model_gen/fan_ctrl_sf.yaml`.

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
| **Enum codegen** | `sf_yaml_to_enum_classdefs()` → classdef `.m`; `sf_yaml_to_sldd_script()` → SLDD init script | `enum_gen.py` |
| **Alt backends** | Mermaid + PlantUML export ✓; PlantUML import (planned) | `stateflow_sir.py`, new `puml_import.py` |

---

## Strategic direction

SIR is a **constrained execution language**, not a mirror of all Stateflow.
By default it supports everything Stateflow supports. Opt-in check sets restrict
what is allowed for specific purposes (formal verification, safety review).

```text
          ┌─── Stateflow generator    (done)
          ├─── Mermaid export         (done)
YAML ─► SIR ─── PlantUML export      (done)
          ├─── PlantUML import ◄──── .puml prototype (planned)
          ├─── YAML normalizer        (planned)
          └─── Formal verification    (future, gated by profile)
```

One YAML definition → multiple consistent outputs. Avoids maintaining a simplified
formal model separately from the production Stateflow model.

---

## Work items

### Priority 1 — Mermaid + PlantUML export (Phase 3) ✓ done

Both diagram backends are implemented in `stateflow_sir.py`:

- `sir_to_mermaid()` / `sf_yaml_to_mermaid()` — structure + transition labels; renders on GitHub and mermaid.live
- `sir_to_puml()` / `sf_yaml_to_puml()` — full fidelity: state hierarchy, transitions, and `en`/`du`/`ex` actions as `entry/do/exit` description lines

Both are exported from `slxgen/__init__.py`. Workflow documented in `docs/workflow.md §4.4`.

**Limitations remaining** — see 1b and 1c below.

### Priority 1b — PlantUML export: history junction

`SIRState.history = True` is silently ignored by `sir_to_puml()`. PlantUML expresses
history as a `[H]` pseudo-state inside the composite state block:

```plantuml
state DMAN {
  [H] --> D_FACE   ' on re-entry resume last child; first entry goes to D_FACE
  state D_FACE
  state D_FOOT
}
```

**Fix** (~5 LOC in `sir_to_puml`): inside `emit()`, when `s.history` is True, emit
`[H] --> <default_child_name>` instead of (or in addition to) `[*] --> <name>`.

The `[H]` pseudo-state replaces the `[*]` initial arrow for states with history —
on first entry PlantUML uses `[H]`'s target; on re-entry it resumes the last active
child.

### Priority 1c — PlantUML ↔ YAML roundtrip (import)

**Motivation**: PlantUML is a natural prototyping format for state machines — easy to
write by hand or with an LLM, renders immediately in VS Code (PlantUML extension),
and supports `entry`/`exit`/`do` action annotations. The intended authoring loop is:

```text
sketch .puml  →  review diagram  →  puml_file_to_yaml()  →  validate  →  run_pipeline()
                      ↑                                            |
                      └──────── edit YAML + sf_yaml_to_puml() ────┘
```

**What to implement** (`slxgen/puml_import.py` — new file):

- `puml_to_sir(text: str) -> SIRModel` — parse PlantUML state diagram text
- `puml_file_to_yaml(puml_path, yaml_path=None) -> str` — write sf.yaml from `.puml`

**PlantUML subset to parse:**

| Construct | Maps to |
| --------- | ------- |
| `@startuml Name` | `SIRModel.name` |
| `[*] --> X` | `X.initial = True` |
| `X --> [*]` | `X.role = 'sink'` |
| `[H] --> X` inside state | parent `history = True`; X `initial = True` |
| `state X { }` | composite state with children |
| `state X` (leaf) | leaf state |
| `--` inside state block | parent `decomp = 'AND'` |
| `X : entry / code` | `X.en` |
| `X : do / code` | `X.du` (convention extension) |
| `X : exit / code` | `X.ex` |
| `src --> dst` | transition (no label) |
| `src --> dst : trigger [cond] / action` | transition with label |

Variables (inputs/outputs/locals) have no PlantUML equivalent — the generated YAML
will have empty variable sections with a comment scaffold for the user to fill in.

**Workflow documentation update** (`docs/workflow.md`): add a "roundtrip" subsection
to §4.4 showing the full edit loop and the `PUML_ONLY` flag pattern.

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

### Priority 5d — Pure-Python layout engine (remove Node.js dependency)

**Motivation:** ELK (Eclipse Layout Kernel) is invoked via `elkjs` (Node.js).
This means every slxgen installation requires a Node.js runtime alongside Python,
which is a friction point in corporate environments, CI images, and embedded
toolchains.  A pure-Python alternative would make `pip install slxgen` sufficient.

**The hard part is compound-state layout.** ELK's compound-node model handles
nested states (states inside states) natively.  Most pure-Python graph libraries
treat nodes as flat — compound support has to be layered on top.  Roughly 30–40 %
of `elk_layout.py` is slxgen-specific post-processing (subchart origin correction,
AND-decomposition, sink placement, label-stagger) that stays regardless of engine.

**Candidate libraries:**

| Library | Type | Compound support | Notes |
| ------- | ---- | ---------------- | ----- |
| [grandalf](https://github.com/bdcht/grandalf) | Pure Python | Partial — recursive calls possible | Sugiyama algorithm; lightest dep; most realistic for compound layout |
| [fast-sugiyama](https://github.com/austinorr/fast-sugiyama) | Pure Python | No — flat only | Fast Sugiyama; needs compound wrapper |
| [python-igraph](https://python.igraph.org/) | C extension (wheels) | No — flat only | Excellent graph analysis + Sugiyama/RT layouts; not pure Python |

**Recommended evaluation order:**

1. **grandalf** — evaluate whether recursive bottom-up calls can replicate ELK's
   compound layout for slxgen's use case (OR states, AND regions, subcharts).
   Prototype: replace `elk_layout_bottomup()` with a grandalf-backed equivalent
   that passes the existing layout acceptance tests.
2. **fast-sugiyama** — if grandalf is too slow for large charts, evaluate as a
   performance alternative with a compound wrapper.
3. **igraph** — consider if only flat-layout quality is the concern (not the
   Node.js dependency), since igraph's Sugiyama is the most polished.

**Acceptance criterion:** all existing generated `.m` files in `example/model_gen/generated/`
have visually equivalent layouts (no overlapping states, similar spacing).

**Risk:** medium.  The feature is self-contained (`elk_layout.py` is the only file
that would change) but the compound-state layout logic is the most
complex part of slxgen.  ELK as fallback can remain for an overlap period.

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
  | omitted + `default_size=None` | not set — Stateflow decides (default = inherited) | no |
  | omitted + `default_size=[1]` | explicit scalar | yes — `'[1]'` |
  | omitted + `default_size=[-1]` | explicit inherited | yes — `'[-1]'` |
  | `[1]` in YAML | explicit scalar | yes — `'[1]'` |
  | `[n, m]` in YAML | n×m array | yes — `'[n m]'` |
  | `[-1]` in YAML | inherited | yes — `'[-1]'` |

  SIR: `SIRVariable.size` is `None` when unspecified (no size key in the
  generated dict → no `Props.Array.Size` emitted). Codegen emits the property
  whenever the `size` key is present, regardless of value.

  **Configuring the default** — pass `default_size` to any entry point:

  ```python
  run_pipeline('my.yaml', default_size=[1])   # force explicit scalar on all unspecified vars
  run_pipeline('my.yaml', default_size=[-1])  # force explicit inherited on all unspecified vars
  run_pipeline('my.yaml')                     # default_size=None — leave it to Stateflow
  ```

  An explicit `size:` in the YAML always takes precedence over `default_size`.

- **Junctions**: decision nodes for shared transition routing — ✓ done (see `junction: true`).
- **Inner transitions**: `inner: true` — stays within source's active child.
- **Named events**: `Stateflow.Event` — no YAML key yet.

### Priority 6b — Parameter / calibration data ✓ done

Parameters are fixed-value data accessible to Stateflow action code — they do not
change at runtime but can be recalibrated between runs.  In MBD / ISO 26262 workflows
they are typically `Simulink.Parameter` objects with a calibration storage class
(e.g. `CalParam`, `ExportedGlobal`).

**YAML schema** — new `params:` section, parallel to `inputs:` / `outputs:` / `locals:`:

```yaml
params:
  - {name: BOOST_THRESH,  type: single,  value: 2.5}
  - {name: FAULT_TIMEOUT, type: uint16,  value: 100,  storage_class: CalParam}
  - {name: GAIN_VEC,      type: single,  value: [1.0, 0.5, 0.25],  size: [3, 1]}
```

**MATLAB codegen** — `Stateflow.Data` with `Scope = 'Parameter'`:

```matlab
p1 = Stateflow.Data(ch);
p1.Name = 'BOOST_THRESH';
p1.Scope = 'Parameter';
p1.DataType = 'single';
p1.Props.InitialValue = '2.5';
```

When `storage_class:` is present, the generator also creates a `Simulink.Parameter`
workspace object and assigns the storage class via `getActiveConfigSet` /
`cscdesigner` API — or writes it to the SLDD if `gen_sldd=True`.

**SIR change** — add `SIRVariable` scope value `'parameter'`; codegen emits
`Scope = 'Parameter'` instead of `'Input'` / `'Output'` / `'Local'`.

**Files to change:** `stateflow_sir.py` (SIRVariable scope), `stateflow.py`
(variable emission loop), `pipeline.py` (SLDD integration).

**Complexity:** low — follows the exact same pattern as `locals:`.

---

### Priority 6c — Graphical functions in Stateflow charts

A Stateflow graphical function is a named callable defined visually inside the
chart.  It has a signature label (e.g. `y = clamp(x, lo, hi)`) and an internal
sub-chart (states + transitions), or a simple flat action body for leaf functions.

**YAML schema** — new top-level `functions:` section:

```yaml
functions:
  clamp:
    signature: "y = clamp(x, lo, hi)"
    inputs:
      - {name: x,  type: single}
      - {name: lo, type: single}
      - {name: hi, type: single}
    outputs:
      - {name: y,  type: single}
    body: "y = min(max(x, lo), hi);"   # leaf function — no sub-states

  routeMode:
    signature: "routeMode(mode)"
    inputs:
      - {name: mode, type: "Enum: FanMode_e"}
    states:                            # compound function — has its own chart
      CHECK: {default: true, en: "..."}
      APPLY: {}
    transitions:
      - {from: CHECK, to: APPLY, condition: valid}
```

**MATLAB codegen:**

```matlab
f1 = Stateflow.Function(ch);
f1.Name = 'clamp';
f1.LabelString = 'y = clamp(x, lo, hi)';
% inputs / outputs as Stateflow.Data on f1
% body or sub-states emitted inside f1
```

**SIR change** — add `SIRFunction` dataclass and `SIRModel.functions` list.

**Files to change:** `stateflow_sir.py`, `stateflow.py`, `stateflow_sir.py` (PlantUML
— functions render as a note or stereotyped state `<<function>>`).

**Complexity:** medium-high for compound functions (recursive states/transitions);
low for flat `body:` functions.  Recommend implementing flat-body case first.

---

### Priority 6d — Requirements linking (reqID on entities)

Traceability from design model entities (states, transitions) back to requirements
is a mandatory deliverable in ISO 26262 and DO-178C workflows.

**YAML schema** — `req:` field on states and transitions (string or list):

```yaml
states:
  ACTIVE:
    req: REQ-CTRL-042
    en: "output = 1;"

transitions:
  - from: IDLE
    to: ACTIVE
    req: [REQ-CTRL-010, REQ-CTRL-011]
    condition: start
    order: 1
```

**Two implementation modes** — offer both, selectable via `run_pipeline` option:

| Mode | Mechanism | Toolbox needed |
| ---- | --------- | -------------- |
| `req_mode='description'` (default) | Embed req IDs in `s.Description` | None |
| `req_mode='slreq'` | Call `slreq.createLink(entity, artifact, id)` | Simulink Requirements |

Description mode (no toolbox):

```matlab
s1.Description = '[REQ-CTRL-042] Active operating mode.';
```

Full `slreq` mode:

```matlab
slreq.createLink(s1, 'MyReqs.slreqx', 'REQ-CTRL-042');
```

**SIR change** — add `SIRState.req: list[str]` and `SIRTransition.req: list[str]`.

**Traceability report** — optionally write a `<stem>_traceability.csv` from the SIR
(no MATLAB required): each row is `(entity_type, entity_id, req_id)`.

**Files to change:** `stateflow_sir.py`, `stateflow.py`, `pipeline.py`.

**Complexity:** low for description mode; medium for `slreq` mode.

---

### Priority 6e — Description and comment fields

Rich text descriptions on states and transitions are useful for design review,
safety analysis, and generated documentation.  Stateflow exposes a `Description`
property on states; transition descriptions can be stored as an annotation or in
a comment convention inside `LabelString`.

**YAML schema** — `desc:` field on states and transitions:

```yaml
states:
  ACTIVE:
    desc: "Active operating mode. Entered when start signal is received and
           self-test has passed. All outputs are driven from this state."
    en: "output = 1;"

transitions:
  - from: IDLE
    to: ACTIVE
    desc: "Start-up transition. Guards: start AND self_test_ok."
    condition: "start && self_test_ok"
    order: 1
```

**MATLAB codegen:**

```matlab
s1.Description = 'Active operating mode. Entered when ...';
```

Transitions in Stateflow do not have a `Description` property — store the `desc:`
value as a MATLAB comment in the generated script directly above the transition
creation block:

```matlab
% IDLE --> ACTIVE : Start-up transition. Guards: start AND self_test_ok.
t3 = Stateflow.Transition(ch);
```

**SIR change** — add `SIRState.desc: str | None` and `SIRTransition.desc: str | None`.

**Generated documentation** — `sf_yaml_to_doc(yaml_path, output_path)` (optional
follow-on): produce a Markdown table of all states + descriptions from the SIR,
no MATLAB required.

**Files to change:** `stateflow_sir.py` (2 fields), `stateflow.py` (2 emit lines).

**Complexity:** very low — one optional field, one conditional codegen line per entity.

---

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

## Documentation improvements

The GitHub Pages site (`huanmel.github.io/slxgen`) is live.  The plan below
describes what to add over time to make it a useful reference for new users.

### Dx1 — Getting-started page

A short `docs/getting-started.md` that covers installation, a minimal YAML, and
a first pipeline run.  Shorter and more task-focused than the full `workflow.md`.

```text
docs/getting-started.md
  1. Install (pip install slxgen, MATLAB setup)
  2. Write a 3-state YAML
  3. sf_yaml_to_puml() → check the diagram
  4. run_pipeline(..., run_matlab=False) → inspect the .m
  5. run_pipeline(..., run_matlab=True) → open the .slx
```

### Dx2 — Screenshots and diagrams

Visual evidence of what the tool produces — the single most useful thing for
a new user evaluating whether to use slxgen.

| Asset | How to create | Where to put |
| ----- | ------------- | ------------ |
| `fanctrl_stateflow.png` | Export PNG from MATLAB/Simulink after running `gen_fan_ctrl.py` | `docs/assets/` |
| `fanctrl_puml.png` | Export from VS Code PlantUML extension (`Alt+D` → save image) | `docs/assets/` |
| `pipeline_diagram.svg` | Draw in any tool (draw.io, excalidraw) showing YAML→SIR→.slx | `docs/assets/` |

Embed in workflow.md or a dedicated gallery page:

```markdown
![FanCtrl in Stateflow](assets/fanctrl_stateflow.png)
```

### Dx3 — Worked example page

A dedicated page that walks through the complete `fan_ctrl` example end-to-end:

1. Read `fan_ctrl_draft.puml` — the hand-drawn prototype
2. Compare with `fan_ctrl_gen.puml` — generated from the YAML
3. Show the generated `FanMode_e.m` classdef
4. Show the Stateflow screenshot

Links to all files in `example/model_gen/`.  The content already exists in
`workflow.md §1.3` — this would extract and expand it into its own page with
images.

### Dx4 — Modeling guidelines page

Extract and publish `docs/stateflow_model_creation_guideline.md` as a
user-facing page.  Currently it is in `docs/` but not linked from `index.md`.
Review for anything project-internal before publishing.

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
