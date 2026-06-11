# slxgen — Authoring Workflow

**Last updated:** June 2026

---

- [slxgen — Authoring Workflow](#slxgen--authoring-workflow)
  - [1. Inputs before you start](#1-inputs-before-you-start)
    - [1.1 For human authors](#11-for-human-authors)
    - [1.2 For LLM-assisted authoring](#12-for-llm-assisted-authoring)
  - [2. The edit-validate loop](#2-the-edit-validate-loop)
  - [3. Validation output — what each message means](#3-validation-output--what-each-message-means)
  - [4. Running the tools](#4-running-the-tools)
    - [4.1 Quick start — full pipeline in one call](#41-quick-start--full-pipeline-in-one-call)
    - [4.2 MATLAB session setup (recommended one-time step)](#42-matlab-session-setup-recommended-one-time-step)
    - [4.3 Validate + generate (explicit pipeline)](#43-validate--generate-explicit-pipeline)
    - [4.4 PlantUML visual preview](#44-plantuml-visual-preview)
    - [4.5 Inspect, compare, and extract](#45-inspect-compare-and-extract)
  - [5. Tips for LLM-assisted authoring](#5-tips-for-llm-assisted-authoring)

---

## 1. Inputs before you start

Do not open an editor until these are ready. Most rework comes from starting YAML
before the requirements are clear.

### 1.1 For human authors

| Input | What to get | Where to find it |
|-------|-------------|------------------|
| Requirements / spec | Modes, triggers, outputs per state | Your system spec document |
| Modeling level | Level 0–3 decision | `docs/stateflow_model_creation_guideline.md` §2 |
| Design rules | Naming, hierarchy, state semantics | `docs/stateflow_model_creation_guideline.md` |
| Tool orientation | Pipeline layers, what each file does | `docs/architecture.md` |
| Reference example | Working YAML with all key fields | `example/model_gen/Ex1_StMach.yaml` |
| Existing model | If reverse-engineering a legacy `.slx` | Use `slx_process()` (see §4) |

From the requirements, answer these before writing YAML:

- What are the top-level operating modes? (→ top-level states)
- Which modes are mutually exclusive? (→ OR decomposition, default)
- Which modes run in parallel? (→ AND decomposition, `type: AND`)
- What triggers each transition? (→ `condition:` field)
- What outputs does each state drive? (→ `en:` actions, output variables)
- Are any states error/fault collectors? (→ `role: sink`)
- Which groups of states belong together? (→ subchart candidates)

### 1.2 For LLM-assisted authoring

Assemble a context packet and include it in the LLM prompt before asking for YAML:

```text
Context packet (in order of importance):
  1. Requirements / spec text          ← what you want to model
  2. docs/stateflow_model_creation_guideline.md   ← design rules
  3. YAML schema summary (see below)   ← what fields are valid
  4. example/model_gen/Ex1_StMach.yaml ← concrete example
  5. Target modeling level (0–3)       ← affects allowed constructs
```

**YAML boolean trap** — PyYAML treats `ON`, `OFF`, `YES`, `NO`, `TRUE`, `FALSE`
(any capitalisation) as boolean values unless quoted. Always quote state names
and transition targets that match these words:

```yaml
# Wrong — ON and OFF silently become Python True / False
states:
  ON:
  OFF:

# Correct
states:
  'ON':
  'OFF':
transitions:
  - from: "ON"
    to:   "OFF"
```

See [ISS-008](../docs/issues.md#iss-008-yaml-boolean-synonyms-silently-corrupt-state-names-and-transition-targets) for full details.

---

**YAML schema summary** (paste this into the LLM prompt):

```yaml
name: ChartName

inputs:
  - {name: signal_in, type: boolean}
  - {name: vec_in, type: single, size: [3, 1]}  # 3×1 vector  → Props.Array.Size = '[3 1]'
  - {name: sc_in,  type: uint8,  size: [1]}    # explicit scalar → Props.Array.Size = '[1]'
  - {name: inh_in, type: single, size: [-1]}   # inherited      → Props.Array.Size = '[-1]'
  - {name: def_in, type: boolean}              # size: omitted  → controlled by default_size

outputs:
  - {name: mode_out, type: uint8, initial_value: 0, size: [1]}  # size: applies to outputs too

locals:
  - {name: counter, type: uint8, initial_value: 0, size: [1]}   # and to locals

  # size: and default_size apply equally to inputs, outputs, and locals.
  # size: omitted + default_size=None (default) → no Props.Array.Size emitted; Stateflow decides
  # size: omitted + default_size=[1]            → Props.Array.Size = '[1]' (explicit scalar)
  # size: omitted + default_size=[-1]           → Props.Array.Size = '[-1]' (explicit inherited)

outputs:
  - {name: mode_out, type: uint8, initial_value: 0}

locals:
  - {name: counter, type: uint8, initial_value: 0}

states:
  STATE_A:
    default: true           # initial state (one per OR parent)
    en: "output = 1;"       # entry action
    du: "counter++;"        # during action
    ex: "counter = 0;"      # exit action
    role: sink              # or: fault, error (aliases for sink)
    subchart: true          # collapsed subchart box
    type: AND               # parallel decomposition (children run simultaneously)
    states:
      CHILD_STATE:
        default: true

transitions:
  - from: STATE_A
    to:   STATE_B
    order: "1"              # execution priority (lower = higher priority)
    condition: "[signal_in]"
    action: "output = 2;"   # runs before target en:
```

---

## 2. The edit-validate loop

The core workflow. Keep the loop tight — structure errors found early are cheap;
layout problems found after generation are expensive.

```text
Step 1 — Gather inputs
  requirements, guidelines, example YAML, modeling level
        │
        ▼
Step 2 — Identify structure from spec
  states, transitions, variables
  (do not write actions yet)
        │
        ▼
Step 3 — Write YAML
  Simple models: write states, transitions, and actions all at once.
  Complex models: skeleton first (states + transitions only),
                  add actions after structure is confirmed.
        │
        ▼
Step 4 — Validate + visual preview
  python example/model_gen/gen_Ex1.py   (or your own gen script)
  → ERRORs and WARNINGs printed

  Recommended: export a PlantUML diagram to verify structure and actions
  before running MATLAB:
    sf_yaml_to_puml(yaml_path, output_path='chart.puml')
  Open in VS Code (PlantUML extension, Alt+D) or paste at plantuml.com.
  Shows topology + en:/du:/ex: action code. No MATLAB required.

  Alternative (structure only, no actions):
    sf_yaml_to_mermaid(yaml_path)  →  stateDiagram-v2 string
  Paste into GitHub, VS Code, or mermaid.live.

  See §4.4 for full PlantUML workflow details.
        │
        ├── ERRORs present → fix and return to Step 4
        ├── WARNINGs present → fix or decide to suppress, return to Step 4
        ├── Visual check fails → fix structure, return to Step 4
        │
        ▼ (clean)
Step 5 — Generate
  sf_yaml_to_matlab(yaml_path, output_path)
        │
        ▼
Step 6 — Review output
  Open PNG screenshot (auto-generated, no extra step)
  Human or multimodal LLM: does the chart topology match the spec?
  Checks 8 and 9 (action semantics) will have fired in Step 4 if actions
  were written in Step 3.
        │
        ├── issues → fix YAML, return to Step 4
        │
        ▼ (approved)
Step 7 — Run in MATLAB + sfLint
  run_pipeline(..., run_matlab=True)  — builds .slx and runs sfLintChart
  sfLint result: <model>_lint.json in the output directory
  Final verification in the Stateflow editor
```

**Simple vs. complex models** — for small, straightforward state machines write
everything in one pass and go straight to Step 4. The two-pass approach (skeleton
first, actions second) pays off when the structure is uncertain: structural ERRORs
are much cheaper to fix before action logic is in place.

---

## 3. Validation output — what each message means

`sir_validate()` prefixes every message with `ERROR` or `WARNING`.
ERRORs abort generation. WARNINGs allow generation but flag design issues.

| Message pattern | Check | Meaning | Typical fix |
|-----------------|-------|---------|-------------|
| `ERROR: transition[N] source 'X' not found` | 1 | `from:` names a state that doesn't exist | Fix the state path (use dotted path for nested states, e.g. `ACTIVE.STARTUP`) |
| `ERROR: transition[N] target 'Y' not found` | 2 | `to:` names a state that doesn't exist | Same as above |
| `WARNING: transition[N] ... no 'order' field` | 3 | Transition has no `order:` — priority undefined | Add `order: "1"` (or Phase 4 normalizer will fill it in) |
| `ERROR: transitions[N] and [M] from 'X' share priority P` | 4 | Two transitions from the same state have the same `order` value | Renumber so each transition from a state has a unique `order` |
| `WARNING: 'X' has multiple default children` | 5 | More than one child has `default: true` | Remove `default: true` from all but the intended initial state |
| `WARNING: 'X' has N children but none is marked default` | 6 | An OR parent has children but no initial state | Add `default: true` to the child that should be entered first |
| `ERROR: state 'X' has both type=AND and subchart=true` | — | Stateflow forbids AND + subchart | Remove one of the two attributes |
| `WARNING: variable 'X' has no initial_value` | 7 | output or local has no default | Add `initial_value:` or suppress check via config if type is enum (defined in SLDD) |
| `WARNING: transition[N] ... action assigns 'var' which target en: also assigns` | 8 | Transition action sets a variable that the target state's `en:` will immediately overwrite | Move the assignment to `en:` only; remove it from the transition action |
| `WARNING: transition[N] ... output 'var' set here but not on path(s) from [...]` | 9 | An output is set on some entry paths to a state but not others — output value depends on how the state was entered | Move the assignment to the target state's `en:` so every entry path gets the same value |

**Suppressing warnings** — configurable via `slxgen_config.yaml` (planned):

```yaml
validator:
  initial_value_required: false      # suppress check 7 for all variables
  redundant_transition_action: off   # suppress check 8
  inconsistent_output_paths: off     # suppress check 9
```

Until the config file is implemented, add a comment in the YAML explaining the
intentional deviation.

---

## 4. Running the tools

### 4.1 Quick start — full pipeline in one call

`run_pipeline()` covers all four steps (validate → generate → MATLAB build → sfLint)
with step-by-step progress output:

```python
from slxgen import run_pipeline

run_pipeline(
    'my_chart.yaml',
    model_name='MyCtrl',
    run_matlab=True,        # False = write .m only, no MATLAB required
    session_name='slxgen',  # shared MATLAB session name
    open_desktop=False,     # True = open full MATLAB GUI when starting new engine
    lint=True,
)
```

See `example/model_gen/quick_start.py` for a working example.

### 4.2 MATLAB session setup (recommended one-time step)

For fast iteration, keep a named MATLAB session open between runs:

```matlab
% Run once in the MATLAB Command Window:
matlab.engine.shareEngine('slxgen')
```

After this, every `run_pipeline(..., run_matlab=True)` call connects in <1 s
instead of starting a cold engine (~15–30 s). The session survives Python
restarts. Without this, `run_pipeline` starts a new engine automatically and
shares it as `'slxgen'`, but that session closes when the Python process exits.

Use `open_desktop=True` when starting a fresh engine from Python to get the full
MATLAB desktop — useful for inspecting the workspace and watching execution.

### 4.3 Validate + generate (explicit pipeline)

```python
# example/model_gen/gen_Ex1.py pattern
from slxgen.stateflow_sir import yaml_to_sir, sir_validate
import yaml, sys

chart_dict = yaml.safe_load(Path('my.yaml').read_text(encoding='utf-8'))
sir = yaml_to_sir(chart_dict)
issues = sir_validate(sir)
errors   = [m for m in issues if m.startswith('ERROR')]
warnings = [m for m in issues if m.startswith('WARNING')]
if errors:
    for msg in errors: print(msg)
    sys.exit(1)
for msg in warnings: print(msg)

from slxgen.stateflow import sf_yaml_to_matlab
sf_yaml_to_matlab('my.yaml', 'out.m')
```

**Generate only (no explicit validation step):**

```python
from slxgen.stateflow import sf_yaml_to_matlab
sf_yaml_to_matlab('my.yaml', 'out.m')
# validation still runs internally; issues printed to stderr
```

### 4.4 PlantUML visual preview

Before running MATLAB, export the YAML to a PlantUML diagram to verify the structure
and action code visually. PlantUML shows `en:`/`du:`/`ex:` actions as state description
lines, which Mermaid cannot do.

**One-liner:**

```python
from slxgen import sf_yaml_to_puml
sf_yaml_to_puml('my_chart.yaml', output_path='my_chart.puml')
```

**Via the gen script** — every `work/*/gen_*.py` script has a `PUML_ONLY` flag at the top:

```python
PUML_ONLY = True   # set to True to regenerate .puml without touching MATLAB
```

Setting it to `True` writes `generated/<chart>.puml` and exits. Set back to `False`
to run the full pipeline.

**Viewing the diagram** — any of these work without installing PlantUML locally:

| Tool | How |
| ---- | --- |
| VS Code | Install the *PlantUML* extension; open the `.puml` file and press `Alt+D` |
| GitHub | Push the `.puml` file; GitHub renders it inline if it's in a `.md` via a fenced block |
| Browser | Paste the text at [plantuml.com/plantuml](https://www.plantuml.com/plantuml) |
| LLM | Paste the text and ask "does this topology match the spec?" |

**What to check in the diagram:**

- All states present and nested correctly
- Initial states (`[*] -->`) point to the right child
- Sink/fault states have `-->  [*]`
- Entry actions (`entry /`) match the output assignments in the spec
- No obvious missing transitions

**Mermaid alternative** — `sf_yaml_to_mermaid()` produces a `stateDiagram-v2` string
that renders on GitHub and mermaid.live. It shows topology and transition labels but
**does not include `en`/`du`/`ex` actions**. Use it for quick structure-only checks
when you do not need to see action code.

---

### 4.5 Inspect, compare, and extract

**Inspect an existing SLX model:**

```python
from slxgen import slx_process
slx_process('model.slx', filters={}, save=True)
# writes JSON + text report + Mermaid block diagram to output dir
# with save=True also exports PNG via MATLAB
```

**Compare two SLX model versions:**

```python
from slxgen import parse_slx, enrich_connections, compare_models
a = enrich_connections(parse_slx('model_v1.slx'))
b = enrich_connections(parse_slx('model_v2.slx'))
diff = compare_models({'v1': a, 'v2': b})
```

**Extract SIR JSON for inspection:**

```python
from slxgen.stateflow_sir import yaml_to_sir
import yaml, json

chart_dict = yaml.safe_load(Path('my.yaml').read_text(encoding='utf-8'))
sir = yaml_to_sir(chart_dict)
print(json.dumps([s.__dict__ for s in sir.states], indent=2))
```

---

## 5. Tips for LLM-assisted authoring

**Structure the requirements as a list, not prose.** Prose hides ambiguity; a list
forces enumeration of states and triggers.

```text
Good input for an LLM:
  Modes: IDLE, CONNECTING, READY, FAULT
  IDLE → CONNECTING when [start_cmd]
  CONNECTING → READY when [link_ok]
  CONNECTING → FAULT when [timeout]
  READY → IDLE when [stop_cmd]
  FAULT → IDLE when [reset_cmd]
  outputs: status_led (uint8, 0=off, 1=yellow, 2=green, 3=red)

Poor input:
  "The system starts idle, then connects when commanded..."
```

**Specify the modeling level** in the prompt. Without it, the LLM will default to
whatever pattern the examples show. Level 1 (industrial) is the right default for
most production models.

**Two-pass authoring works better than one-pass.** Ask for the skeleton first
(states + transitions, no actions), validate it, then ask the LLM to add actions
in a second prompt. This prevents action logic from masking structural errors.

**Use PlantUML as the fast feedback loop.** Before running MATLAB, export the YAML to
PlantUML and paste it into the LLM conversation:

```text
"Here is the PlantUML diagram of the generated state machine.
Does the topology match the requirements?
Are all states reachable? Are the entry actions correct?"
```

This loop (edit YAML → export `.puml` → review with LLM) is much faster than
running the full pipeline and catches structural issues before MATLAB is involved.

**Use the Stateflow screenshot for final review.** After running the full pipeline,
open the PNG and check the layout:

```text
"Here is the generated Stateflow chart. Does the topology match the requirements?
Are all states reachable? Does the layout look reasonable?"
```

A multimodal LLM can answer these questions from the image without running MATLAB.

**Validation errors are cheap to fix early.** If the LLM produces YAML with ERROR
messages, paste the error output back and ask for a fix before proceeding to
generation. Do not ask the LLM to "generate anyway" — ERRORs indicate structural
problems that will produce incorrect charts.
