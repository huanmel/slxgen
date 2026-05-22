# slx2txt — Developer Notes

Comprehensive notes for continuing development. Captures architecture,
Simulink XML quirks, all non-obvious implementation decisions, and known
gaps discovered during the sl2py session (May 2026).

---

## Project structure (files to move)

```
slx2txt/
  slx2txt.py          ← entire library (one file)
  cli_slx2txt.py      ← empty placeholder for CLI

data/
  slx_filters_default.yml   ← default filter config (referenced by notebooks)
  model/pid_control_ex1.slx ← test model (used in slx2txt __main__ block)

notebooks/
  process_model.py    ← main processing script (hardcodes model paths)
  compare_models.py   ← compares two model slim dicts
  compare_trees.py    ← compares model trees
```

No shared code with `ddgen/`. Dependencies: stdlib + `pyyaml` only.

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

## Known gaps / future work

- **MATLAB Function blocks**: listed in machine.xml but not parsed (different
  internal structure, not standard Stateflow states/transitions)
- **sf.yaml → SLX creation**: `stateflow_dict_to_matlab(chart_dict)` generates a MATLAB
  `.m` script using the Stateflow API. `sf_yaml_to_matlab(yaml_path)` reads a yaml and
  returns the script. `slx_process(..., outputs=['sf.m'])` saves it alongside sf.yaml.
  Limitation: `sfAutoArrange` is called at the end but manual layout may still be needed.
- **ReferencedSubsystem charts**: charts inside cross-file referenced subsystems
  (e.g. `ClimCtl_sub`) are found when that SLX is processed by `process_model_tree`.
  They do NOT appear in the parent model's sf.yaml export.
- **Transition label parsing**: `_parse_transition_label()` now correctly splits
  `after(InitTout,tick)[HMI_Online && ~VerCheckOk]{HMI_fault=...}` into
  `trigger`, `condition`, and `action` fields. `trigger` appears in sf.yaml and report.txt.
- **`_arch.md` output**: Mermaid diagram generation exists but was disabled in
  `process_model.py` for current workflow (user only needs report.txt + sf.yaml)
- **No `__init__.py`**: notebooks use `sys.path.insert` hack. Add `__init__.py`
  to make it a proper package

---

## Test model paths (as of May 2026)

```python
# Primary test model (HMI driver for dp187_ravo project)
MODEL_PATH = r'C:\Users\ivanm\Documents\MATLAB\EKL\dp187_ravo\dp187_csw\.deps\comps\drivers\hmidrvrdp187\models\HMIDrvrDp187_sub.slx'
PROJ_ROOT  = r'C:\Users\ivanm\Documents\MATLAB\EKL\dp187_ravo\dp187_csw\.deps\comps\drivers\hmidrvrdp187'

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
