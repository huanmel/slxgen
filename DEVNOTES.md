# slxgen — Developer Notes

Comprehensive notes for continuing development. Captures architecture,
Simulink XML quirks, all non-obvious implementation decisions, and known
gaps discovered during the sl2py session (May 2026).

---

## Project structure

```
slxgen/
  slx2txt.py          ← entire library (one file)
  stateflow.py        ← stateflow parsing and MATLAB generation
  cli.py              ← CLI entry point

data/
  slx_filters_default.yml   ← default filter config (referenced by work scripts)
  model/pid_control_ex1.slx ← test model (used in slx2txt __main__ block)

work/
  process_model.py              ← main processing script (hardcodes model paths)
  compare_models.py             ← compares two model slim dicts
  compare_trees.py              ← compares model trees
  sf_yaml_to_matlab_HMI_StMach.py ← generate MATLAB script from sf.yaml
  sf_export_charts.m            ← export all Stateflow charts in a model to PNG
```

Dependencies: stdlib + `pyyaml` only. No shared code with `slddgen/` or `dbcgen/`.

---

## Development environment

**Python** — miniforge3 `base` conda environment (`C:\Users\ivanm\miniforge3\python.exe`).
All three generator projects are installed as editable packages into `base`:

```
pip install -e C:\D\proj\gh\slxgen
pip install -e C:\D\proj\gh\slddgen
pip install -e C:\D\proj\gh\dbcgen
```

After that, `from slxgen import ...` works anywhere without `sys.path` hacks.
To reinstall after a fresh clone: `pip install -e .` from inside each repo root.

**MATLAB** — R2024a. A MATLAB MCP server is available for automated execution:

- `evaluate_matlab_code` — run inline MATLAB commands
- `run_matlab_file` — run a `.m` script
- `check_matlab_code` — static analysis

---

## Processing pipeline

```
parse_slx(slx_path)
  └─ load_stateflow_machine(z)          # machine.xml → {sf_path: chart_id}
  └─ parse_system(root, z, ref, sf_machine, sf_path='')   # recursive
        ├─ bus element renaming (Inport/Outport with InterfaceData)
        ├─ stateflow chart lookup by full sf_path key
        └─ recurse into embedded subsystems (same SLX only)

enrich_connections(model)               # resolves Goto/From, builds connection_info

filter_model_data(model, filters)       # removes skip_blocks, keeps only listed params
  └─ SourceType fallback for Reference blocks

→ outputs via slx_process():
    report.txt, arch.md, slim.json, slim.min.json, full.json, sf.yaml
```

---

## Key Simulink XML facts (hard-won)

### SLX is a ZIP
- `simulink/blockdiagram.xml` — top-level system ref
- `simulink/systems/system_root.xml` (or named ref) — root block list
- `simulink/systems/system_NNNN.xml` — per-subsystem XML
- `simulink/stateflow/machine.xml` — chart registry
- `simulink/stateflow/chart_NN.xml` — per-chart states/transitions

### BlockType="Reference" for all library blocks
ALL blocks sourced from the Simulink library are `BlockType="Reference"` in XML.
The semantic type lives in the `SourceType` parameter.
Examples: `Enumerated Constant`, `Compare To Constant`, `Unit Delay`, etc.

**Filter fix**: after checking `blk['type']`, also check `blk['parameters']['SourceType']`
for Reference blocks. This lets YAML entries like `Enumerated Constant: [Value]` work.

**Report fix**: display `SourceType` as the type tag (`[Enumerated Constant]`)
instead of the meaningless `[Reference]`. Drop `SourceType` and `SourceBlock`
from parameter display since `SourceType` is now the tag.

### Enumerated Constant: Value is in InstanceData
```xml
<Block BlockType="Reference" Name="Enumerated Constant1">
  <P Name="SourceType">Enumerated Constant</P>
  ...
  <InstanceData>
    <P Name="Value">HMIClimModeReq_en.AUTO</P>   ← here, not in <P>
  </InstanceData>
</Block>
```
Parser puts `InstanceData/P` into `blk['instance_data']` (separate from `blk['parameters']`).
Filter applies the same `pk` list to both. Both are merged for display.

### Default parameter omission
Simulink omits parameters that equal their default value:
- `Logic` blocks: `Operator=AND` is the default → absent from XML → inject it explicitly
- `RelationalOperator` blocks: `Operator===` is the default → same

**Fix in filter step**:
```python
_OP_DEFAULTS = {'Logic': 'AND', 'RelationalOperator': '=='}
```

### Bus element ports (BusElementIn* / BusElementOut*)
Simulink auto-generates `Inport`/`Outport` blocks named `BusElementIn23` etc.
for bus selector wiring. The real signal identity is in `InterfaceData`:
```xml
<Block BlockType="Inport" Name="BusElementIn23" SID="10410">
  <List ListType="InterfaceData">
    <P Name="PortName">DrvrIn</P>
    <P Name="Element">VCU_ClimModeReq</P>
  </List>
</Block>
```
**Fix**: rename at parse time to `PortName.Element` (e.g. `DrvrIn.VCU_ClimModeReq`).
Done in `parse_system()` for all `Inport`/`Outport` blocks with `InterfaceData`.

Also: `_format_port()` previously returned `blk['name']` for Inport/Outport blocks,
causing doubled names like `DrvrIn.VCU_ClimModeReq.DrvrIn.VCU_ClimModeReq` in
connection strings. Removed that special case — now returns `Out1`/`In1`.

### Fan-out connections (nested Branch elements)
Simulink Line XML nests `<Branch>` elements recursively for fan-out signals:
```xml
<Line>
  <P Name="Src">10410#out:1</P>
  <Branch><P Name="Dst">10412#in:1</P></Branch>         ← level 1
  <Branch>                                               ← level 1 (no Dst)
    <Branch><P Name="Dst">10837#in:1</P></Branch>        ← level 2
    <Branch><P Name="Dst">10872#in:1</P></Branch>        ← level 2
  </Branch>
</Line>
```
**Fix**: use `.//Branch` (recursive) not `./Branch` (direct children only).

### Stateflow chart linking via machine.xml
`machine.xml` maps instance names → chart IDs. Instance names use **full subsystem paths**:
```xml
<instance id="160">
  <P Name="name">CONTROL/Chart</P>    ← NOT just "Chart"
  <P Name="chart">136</P>
</instance>
<instance id="178">
  <P Name="name">CONTROL/Chart1</P>
  <P Name="chart">161</P>
</instance>
<instance id="197">
  <P Name="name">HMI_UI_IN_PROC/RST_MULTIPLE1/Chart</P>
  <P Name="chart">179</P>
</instance>
```
**Fix**: `parse_system()` now has `sf_path=''` parameter. On each recursion into a
subsystem it builds `child_sf_path = f"{sf_path}/{blk['name']}"`. Chart lookup uses
`sf_machine.get(blk_sf_key)` where `blk_sf_key = f"{sf_path}/{blk['name']}"`.

Root-level charts (e.g. `HMI_StMach`) have no path prefix and match directly.

### Block names with embedded newlines
Some block names and parameter values contain `&#xA;` (newline) in XML:
`Name="Enumerated&#xA;Constant1"` → Python string `'Enumerated\nConstant1'`
`SourceBlock="simulink/Sources/Enumerated\nConstant"` → same issue

**Fix**: `_clean(name)` replaces `\n` with space. Applied to block names and
parameter values in report rendering.

### Commented-out (disabled) blocks
Blocks disabled in Simulink have `<P Name="Commented">on</P>`.
They are excluded from code generation.
**Fix**: filter step detects this and returns a minimal dict with `'disabled': True`.
Report shows them in a separate `DISABLED` section.

---

## Stateflow parsing

### State hierarchy from XML nesting
State parent-child relationships come from XML element nesting, NOT from SSID values.
SSID values are sequential integers with no hierarchy info.

```xml
<state SSID="5">
  <P Name="labelString">NORMAL
en:HMI_fault=HMIDrvr_Fault_enum.NA;</P>
  <Children>
    <state SSID="6">
      <P Name="labelString">ON
en:st_cmd=HMIStCmd_en.ON;</P>
      <Children>
        <state SSID="7">...</state>
      </Children>
    </state>
  </Children>
</state>
```

State `labelString` format: first line = state name, rest = actions.
Actions use Stateflow keyword prefixes: `en:`, `du:`, `ex:`, `en,du:`, etc.

### Default state detection
The default (initial) state has a **default transition**: a `<transition>` element
with no `src/P[@Name='SSID']`. The `dst/P[@Name='SSID']` points to the default state.

### Action string format (real examples)
```
'en:\nst_cmd=HMIStCmd_en.ON;\nsend_cmd=true;'   → keyword alone, actions follow
'en:st=HMIDrvr_State_enum.ON_INIT;'              → inline single action
'en:st_cmd=HMIStCmd_en.OFF;\nHMI_fault=...'      → inline first + continuations
```

### AND_STATE vs OR_STATE
Parallel states: `<P Name="type">AND_STATE</P>`. Tagged `[AND]` in report.
Exclusive states (default): OR_STATE or absent.

### MATLAB Function blocks
`machine.xml` also lists MATLAB Function blocks as chart instances with names like
`HMI_OUT_PROC/MATLAB Function`. These have `SFBlockType=MATLAB Function`, not `Chart`.
Current parser only processes `SFBlockType=Chart` blocks — MATLAB Functions are skipped.

---

## sf.yaml export format

```yaml
name: CONTROL/Chart1          # full qualified path from Stateflow
inputs:
- {name: signal_in, type: 'Inherit: Same as Simulink'}
outputs:
- {name: signal_out, type: 'Inherit: Same as Simulink'}
states:
  NORMAL:                     # root state, no default/type → OR_STATE
    en: "HMI_fault=NA;"       # single-line: plain scalar
    states:
      ON:
        en: |-                # multi-line: YAML literal block scalar
          st_cmd=ON;
          send_cmd=true;
        states:
          ON_INIT:
            default: true     # only present when true
            en: st=ON_INIT;
      OFF:
        type: AND             # only present for AND_STATE
transitions:
- from: NORMAL.ON.ON_INIT     # full dotted path
  to: NORMAL.ON.ON_INIT_ONLINE
  condition: HMI_Online       # omitted if empty
  action: isFault=false;      # omitted if empty
  order: '2'                  # string from XML
```

Uses `_SFYamlDumper` (custom Dumper) to force literal block scalars (`|-`) for
multi-line strings — avoids PyYAML single-quoted scalar blank-line artefact.

Filename: sanitized full chart path, e.g. `CONTROL_Chart1_sf.yaml`.
Multiple charts in the same model each get their own file.

---

## Filter config (slx_filters_default.yml)

```yaml
default_attrs:    # kept for every block regardless of type
  - name
  - type
  - input_ports
  - output_ports

default_params: []   # params kept for ALL block types (rarely used)

block_types:         # extra params per type (or SourceType for Reference blocks)
  Constant:                [Value]
  Gain:                    [Gain]
  Sum:                     [Inputs]
  Saturate:                [UpperLimit, LowerLimit]
  Switch:                  [Criteria, Threshold]
  Lookup_n-D:              [Table, BreakpointsForDimension1, BreakpointsForDimension2]
  PreLookup:               [BreakpointsData]
  Interpolation_n-D:       [Table]
  RateLimiter:             [RisingSlew, FallingSlew]
  DiscreteIntegrator:      [gainval, InitialCondition]
  TransferFcn:             [Numerator, Denominator]
  PID Controller:          [P, I, D, N, Form, TimeDomain]
  Discrete PID Controller: [P, I, D, N, Form, TimeDomain]
  From:                    [GotoTag]
  Goto:                    [GotoTag]
  EnablePort:              [StatesWhenEnabling]
  SubSystem:               [ReferencedSubsystem]
  Reference:               [SourceBlock, SourceType]    # SourceType shown as type tag
  Enumerated Constant:     [Value]                      # matched via SourceType lookup
  Chart:                   [SFBlockType]
  MinMax:                  [Function]
  Logic:                   [Operator]
  RelationalOperator:      [Operator]

skip_blocks:          # removed entirely; connections stitched A→skip→B
  - SignalConversion
  - DataTypeConversion
  - Terminator
```

**SourceType lookup**: for `Reference` blocks, the filter also checks
`blk['parameters']['SourceType']` against `block_types`. This makes entries like
`Enumerated Constant: [Value]` work without needing to know the underlying XML type.

---

## Report text format

```
=== root > SubsystemName ===      ← breadcrumb header (each subsystem expanded inline)

INPUTS:  sig1 | sig2 | Bus.Field  ← Inport block names (renamed from BusElementIn*)

DISABLED (commented-out, not in code gen):
  BlockName  [BlockType]

BLOCKS:
  [Gain]  Gain1  Gain=single(10)
  [Logic]  Logical Operator  Operator=AND
  [Enumerated Constant]  Enumerated Constant2  Value=HMIClimModeReq_en.AUTO
  [RelationalOperator]  Relational Operator3  Operator===

VIRTUAL SIGNALS (Goto→From):
  GotoTag  GotoBlock1, GotoBlock2 → FromBlock1, FromBlock2

SIGNAL FLOW:
  Source.Out1 → Dest.In2
  Bus.Field.Out1 → Relational Operator3.In1    ← renamed bus element

=== root > SubsystemName [Stateflow] ===

INPUTS:  in_HMI_st | InitTout
OUTPUTS: st_cmd | HMI_fault

STATES:
  NORMAL  (NORMAL)
    en:
      HMI_fault=NA;
    ON  (NORMAL.ON)
      en:
        st_cmd=ON;
      ON_INIT  (default, NORMAL.ON.ON_INIT)
        en:
          st=ON_INIT;

TRANSITIONS:
  NORMAL.ON.ON_INIT  --[HMI_Online]-->  NORMAL.ON.ON_INIT_ONLINE
```

---

## Key function signatures (public API)

```python
# Parse one SLX, save outputs, return slim dict
slim = slx_process(slx_path, filters, save=True, output_dir=None, outputs=None)
# outputs list: ['report.txt', 'arch.md', 'slim.json', 'slim.min.json', 'full.json', 'sf.yaml']

# Process model + all referenced sub-models recursively
results = process_model_tree(slx_path, filters, proj_root,
                             save=True, output_dir=None,
                             outputs=None, parse_libraries=False)
# returns {model_name: slim_dict}

# Low-level
full  = parse_slx(slx_path)               # raw parse (no filter)
full  = enrich_connections(full)           # resolve Goto/From, build connection_info
slim  = filter_model_data(full, filters)  # apply filter config

# Render (no file I/O)
text  = model_to_text(slim, path='ModelName')
md    = model_to_markdown(slim, title='ModelName', max_depth=2)

# Stateflow export
chart_dict = stateflow_chart_to_dict(sf)   # sf = blk['stateflow'] from slim
```

---

## Stateflow generation round-trip

Full workflow from source SLX to a regenerated model with visual review:

### Step 1 — Extract sf.yaml from SLX (Python)

```python
# produces HMIDrvrproject_ID_sub_reports/HMI_StMach_sf.yaml
python work/process_model.py
# or via API:
from slxgen import slx_process
slx_process(slx_path, filters, outputs=['sf.yaml'])
```

### Step 2 — Generate MATLAB script from sf.yaml (Python)

```python
# produces HMIDrvrproject_ID_sub_reports/HMI_StMach_sf.m
python work/sf_yaml_to_matlab_HMI_StMach.py
# or via API:
from slxgen import sf_yaml_to_matlab
sf_yaml_to_matlab(yaml_path, output_path=output_path)
```

### Step 3 — Run the script in MATLAB

In MATLAB (or via MCP `evaluate_matlab_code`):

```matlab
run('path\to\HMI_StMach_sf.m')
% Creates HMI_StMach.slx in the current working directory.
% Expected warnings:
%   "Automated layout might not improve upon original layout" — cosmetic
%   "underspecified signal dimensions" — expected, no plant connected
```

### Step 4 — Export charts to PNG for review

```matlab
% addpath to work/ folder first
pngPaths = sf_export_charts('HMI_StMach');
% or with explicit output dir:
pngPaths = sf_export_charts(modelPath, outputDir);
```

`sf_export_charts.m` finds all `Stateflow.Chart` objects in the model, exports
each to a PNG named after its full hierarchy path (e.g.
`HMI_StMach_HMI_StMach.png`), and prints the paths. The PNG can be read back
by Claude (multimodal) for automated layout review without manual screenshots.

---

## ELK-based layout (as of 2026-05)

Stateflow chart layout is computed by ELK (Eclipse Layout Kernel) via `elkjs` npm,
replacing the old `_compute_sf_layout()` BFS grid.

### Pipeline

```
chart_dict  →  sf_to_elk_json()  →  elk_layout()  →  elk_to_stateflow_layout()
                elk_layout.py       node elk_runner.js    elk_layout.py
                                                              ↓
                                              positions + edge_routing
                                                              ↓
                                         stateflow_dict_to_matlab()  (stateflow.py)
```

### Tuned defaults (from 37-variant sweep on HMI_StMach)

| Setting | Value | Why |
| ------- | ----- | --- |
| `label_substitution=True` | default | Omit label dims from ELK — routes geometrically, 40% narrower containers. Any label (even 1px) causes ELK to insert dummy routing nodes adding 600-700px height. |
| `nodePlacement.strategy` | `LINEAR_SEGMENTS` | Straightens state chains |
| `nodePlacement.alignment` | `CENTER` | Prevents zigzag |
| `edgeRouting` | `SPLINES` | Smooth Bezier, closest to native Stateflow style |
| `spacing.nodeNode` | `50` | Compact but readable |
| `nodeNodeBetweenLayers` | `60` | Compact vertical separation |
| Compound top padding | dynamic | `_compound_header_h(body)` computes actual header height from en/du/ex action line count — fixed ON_INIT overlapping parent header |
| `_ELK_LABEL_MID_X = 10` | LCA-relative | MidPoint x override for labeled transitions — labels start at left margin and extend rightward instead of overflowing from arc center |

### Override mechanism (`elk_options=` to `sf_yaml_to_matlab()`)

All `__` keys are intercepted before passing remaining options to ELK:

| Key | Default | Description |
| --- | --- | --- |
| `__direction__` | `DOWN` | Layout axis for normal states |
| `__max_label_width__` | `150` | Pixel cap for transition label width |
| `__label_substitution__` | `true` | Replace long labels with short IDs for ELK sizing |
| `__bare_transitions__` | `false` | Skip all transition geometry — Stateflow auto-routes |
| `__sink_placement__` | `none` | Post-ELK sink repositioning: `right`, `left`, `top`, `bottom`, `auto`, `none` |
| `__auto_sink__` | off | Integer N: auto-promote pure-sink states with ≥ N incoming transitions |
| `__sink_bus_junctions__` | `false` | Route sink transitions through a vertical junction bus spine |
| `__orthogonal_junctions__` | `false` | Strict H/V spine (requires `__sink_bus_junctions__`) |
| `__subchart_leaf_size__` | off | `WxH` e.g. `200x150` — override subchart footprint at parent level |

### Known remaining layout issues

1. **Multiple transitions to the same destination at the same y**: two fault
   transitions from ON→FAULT_ACTIVE both land at `MidPoint=[10, 494]`. Labels overlap.
   Root cause: topology (same src/dst tier), not layout. Fix: semantic role classification
   to route fault transitions laterally, not downward.

2. **Long labels overflow right edge**: 75-char labels (≈525px) exceed container width
   (≈430px). Labels start at x=10 so only ~95px overflows. Acceptable for now.

3. **Junction bus below-gateway edge case**: source states positioned below the gateway
   junction produce an upward arc instead of a straight horizontal. Functional but not
   perfectly orthogonal.

### Variant research script

`work/test_elk_variants.py` — 37 variants, model-agnostic.

```
python work/test_elk_variants.py path/to/chart_sf.yaml    # summary table
python work/test_elk_variants.py --write                   # write .m files
python work/test_elk_variants.py --log results.csv         # cross-model CSV
```

---

## Known gaps / future work

### Layout — next improvements (medium priority)

- **Overlapping transition labels**: two transitions to the same destination land at
  the same `MidPoint.y`. Cause: topology, not ELK. Fix: detect fan-in transitions at
  codegen time and stagger their `MidPoint` vertically (e.g. ±20 px per transition index).
- **Back-edge arcs cut through states**: ELK routes them internally; Stateflow convention
  is to route around the outside via a junction. Fix requires generating explicit junction
  nodes for detected back-edges (high effort).
- **State box sizing for multi-line action text**: character-count estimate can produce
  slightly undersized boxes. Manual `width`/`height` YAML overrides are the workaround.
  A more accurate heuristic could count wrapped lines using a fixed char-per-line estimate.

### YAML spec coverage — missing features

- **Junctions**: decision nodes for shared transition routing; not yet emittable from YAML.
- **Inner transitions**: `inner: true` on a transition stays within the source state's
  active child without exiting the source. Not in YAML schema or codegen.
- **Named events / messages**: Stateflow events (`Stateflow.Event`); currently no YAML key.

### SIR normalization layer (`slxgen/stateflow_sir.py`)

Phases 1–2 complete:

- `yaml_to_sir()` normalises YAML into a flat typed graph
- `sir_validate()` runs structural checks (7 rules)
- `sir_to_chart_dict()` converts back so existing codegen is unchanged

Next phases:

- **Phase 3**: Mermaid diagram output from `SIRModel` (~50 LOC, proves multi-target output)
- **Phase 4**: YAML auto-normalisation (sort states, canonical key order)
- **Phase 5**: Predicate/timer extraction as SIR transforms; `sir_to_chart_dict()` handles
  the translation — `stateflow_dict_to_matlab()` stays frozen

### SLX parsing — deferred

- **MATLAB Function blocks**: listed in `machine.xml` but not parsed (different internal
  structure, not standard Stateflow states/transitions).
- **ReferencedSubsystem charts**: charts inside cross-file referenced subsystems are found
  when that SLX is processed by `process_model_tree`; they do NOT appear in the parent
  model's `sf.yaml` export.
- **`_arch.md` output**: Mermaid diagram generation exists but is disabled in
  `process_model.py` for current workflow (only report.txt + sf.yaml needed).

---

## Test model paths (as of May 2026)

```python
# Primary test model (HMI driver for example project)
MODEL_PATH = r'C:\Users\ivanm\Documents\MATLAB\EKL\myproject\project_ID_csw\.deps\comps\drivers\hmidrvrproject_ID\models\HMIDrvrproject_ID_sub.slx'
PROJ_ROOT  = r'C:\Users\ivanm\Documents\MATLAB\EKL\myproject\project_ID_csw\.deps\comps\drivers\hmidrvrproject_ID'

# Small test model in repo
data/model/pid_control_ex1.slx
```

---

## Git log (sl2py, recent commits relevant to slx2txt)

```
09833b1  Fix Stateflow chart lookup for blocks inside subsystems
1b43a34  Add Stateflow YAML export (sf.yaml output type)
475be59  Use SourceType as display type for Reference blocks in report
79fc33d  Show Value for Enumerated Constant blocks via SourceType filter lookup
ba71d6f  Fix missing fan-out connections from nested Simulink branches
e22b90f  Rename bus element ports from BusElementIn* to PortName.Element
238c83c  detect and surface Simulink commented-out blocks in report
e278ca0  Filter out comment rows from CSV input in create_pars_entries_from_file
a11bc29  Add SLDD generation support and refactor coder type handling
b73a1aa  use full dotted paths for nested Stateflow states
```
