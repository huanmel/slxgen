# SIR Design Notes

Design notes and roadmap for the Stateflow Intermediate Representation (SIR) —
a normalization layer between the YAML authoring format and MATLAB codegen.

---

## Implementation status

| Phase | What | Status |
| ----- | ---- | ------ |
| 1 | `stateflow_sir.py`: dataclasses + `yaml_to_sir()` + `sir_validate()` + JSON export | **Done** |
| 2 | `sir_to_chart_dict()` — SIR drives generation (not just validation) | **Done** |
| 3 | Second codegen target (Mermaid state diagram) | Planned |
| 4 | Predicate/timer extraction — parse temporal conditions into typed SIR nodes | Planned |

**Live pipeline** (as of Phase 2):

```text
sf_yaml_to_matlab(yaml_path)
  → yaml.safe_load()                # raw chart_dict
  → yaml_to_sir(chart_dict)         # normalize into flat SIRModel
  → sir_validate(sir) → stderr      # structural checks; generation continues
  → sir_to_chart_dict(sir)          # flat SIR → nested dict for codegen
  → stateflow_dict_to_matlab(cd)    # unchanged MATLAB codegen
```

Example scripts: `example/model_gen/gen_Ex1_sir.py` (SIR inspection + round-trip)
and `example/model_gen/gen_Ex1.py` (end-to-end generation with SIR active).

---

## Why SIR over raw YAML

YAML remains the authoring format — for both humans and LLMs. It is concise and
quick to write or generate. The SIR is not a replacement for YAML authoring; it is
a processing layer that sits between the YAML file and generation.

The problem is not YAML itself but the raw Python dict produced by `yaml.safe_load()`:
state IDs are implicit (nested keys, no dotted paths), transition targets are unchecked
strings, priorities are untyped, and variable scopes are scattered across three separate
lists. The SIR normalizes all of this before any downstream tool sees it.

Intended workflow:

```text
human / LLM  →  writes YAML  →  yaml_to_sir()  →  sir_validate()  →  model generation
(authoring)      (stays as-is)   (normalization)    (catches errors)
                                       ↓
                             JSON export (optional, for inspection only)
```

The JSON is an inspection artifact — useful for debugging the SIR or feeding a
separate tool, not something anyone would author directly.

| Concern | Raw YAML | SIR |
| ------- | -------- | --- |
| State addressing | Nested keys, no canonical IDs | Flat list, dotted IDs (`ACTIVE.STARTUP.INIT`) |
| Transition source/target | Opaque strings | Validated against actual state set |
| Priority conflicts | Silent wrong behavior | Detected before codegen |
| Variable completeness | Anything goes | Typed, scoped, missing `initial_value` flagged |
| Default-substate hygiene | Unchecked | Multiple / missing defaults reported |
| Cross-cutting queries | Requires tree traversal | Direct list iteration |
| Multiple output targets | Impossible without re-parsing | Walk `sir.states` / `sir.transitions` once |
| Structural diff / merge | Hard on nested dicts | Straightforward on flat lists with stable IDs |

### What `sir_to_chart_dict()` enables

The function (~65 LOC) converts the flat SIRModel back to the nested dict shape
expected by `stateflow_dict_to_matlab()`. This means:

- **The SIR is the authoritative source for generation**, not just validation.
- ELK layout functions are unaffected — they receive the reconstructed nested dict
  unchanged.
- Any future schema extension (e.g. adding `initial_value` enforcement) only touches
  `yaml_to_sir()` and `sir_to_chart_dict()`; the 318-line codegen never changes.
- Round-trip parity is verified: `yaml_to_sir → sir_to_chart_dict` produces a dict
  that generates byte-for-byte identical MATLAB output to the direct YAML path.

### Why NOT `sir_to_matlab()` yet

A direct `sir_to_matlab()` rewrite (~500–800 LOC) would:

- Replicate all geometry, ELK integration, subchart, and junction logic
- Have no regression test coverage at launch
- Require constant updates as the SIR schema evolves (Phases 3–4)

The `sir_to_chart_dict()` bridge defers this work until the SIR schema is stable
and a new backend actually provides something the current path cannot.

---

## Goal

Insert an explicit IR pass between YAML parsing and MATLAB generation:

```text
YAML (authoring)
   ↓  normalization pass
SIR (in-memory, validated)
   ↓  codegen
MATLAB script → Stateflow
```

Current pipeline (`stateflow.py`) goes directly YAML dict → MATLAB strings,
mixing validation, semantic resolution, and generation. An SIR separates these
and enables a future validation backend, multiple codegen targets, and cleaner
LLM generation.

---

## What the normalization pass does

### States

- Flatten YAML nesting into a flat list of nodes with explicit `parent` references.
  The YAML already uses dotted IDs (`ACTIVE.STARTUP.CONNECTING`) in transitions —
  the SIR just makes this canonical for states too.
- Preserve all YAML fields including layout hints (`role`, `subchart`) — these are
  not execution semantics but must survive the pass for downstream consumers
  (ELK layout, Stateflow API calls like `makeAtomic`).

### Transitions

- Map `order: '1'` → `priority: 1` (integer). Convention: **lower number = higher
  priority**, matching Stateflow's own ordering and the existing YAML.
- Mark action type explicitly: `CONDITION` (safe before junctions) or `TRANSITION`
  (final segment, after junctions). See guideline section 4.6–4.7.
- Preserve the `trigger:` field separately from `condition:` — Stateflow treats
  event triggers and data conditions differently (e.g.,
  `trigger: after(startupTout,tick)` is an event, not a data guard).

### Predicates

Extract inline conditions into named predicate objects **selectively**, not
universally:

| Condition type | Action |
| -------------- | ------ |
| Simple signal ref: `devOnline`, `hasFault` | Keep inline — no extraction needed |
| Compound boolean: `devOnline && devStatus == CONNECTED` | Extract if reused, keep inline if single-use |
| Temporal expression: `count(x) > t`, `duration(x) > t`, `after(n, tick)` | Always extract into a predicate object |

Inline temporal logic is fully valid Stateflow — `count(u > 0) > timeout` is a
single temporal predicate where the inner expression `u > 0` is evaluated within
the operator context. Do not try to recursively decompose the inner expression into
a separate predicate reference.

Predicate object fields:

```text
id            — P_<auto> or P_<name> if named
type          — PURE | TEMPORAL
expression    — full source expression (kept verbatim for PURE)
temporal.mode — COUNT | DURATION | AFTER  (match the operator used)
temporal.signal_expr — the boolean expression inside the operator
temporal.threshold   — the timeout value / variable name
```

`COUNT` mode means tick-based; `DURATION` means seconds. Do not convert between
them — they have different semantics. Preserve whichever the YAML uses.

### Variables

Add fields the YAML currently omits:

- `initial_value` — required; generator must error if absent for locals and outputs
- `scope` — LOCAL | OUTPUT | INPUT (maps directly to YAML `locals:`, `outputs:`,
  `inputs:` sections — do not flatten these into a single `variables:` list)
- `reset_owner` — which state's entry action is the designated reset site

### Execution model

Correct cycle order for the validation spec (not a runtime — Stateflow handles
execution; this describes what a validator should check for correctness):

```text
INPUT_SAMPLING
TIMER_UPDATE          ← must precede predicate evaluation
PREDICATE_EVALUATION  ← may depend on updated timer values
TRANSITION_COLLECTION
CONFLICT_RESOLUTION   ← lower priority number wins
TRANSITION_EXECUTION  ← exit → condition/transition action → entry
STATE_UPDATE
OUTPUT_COMPUTATION
```

The order `TIMER_UPDATE` before `PREDICATE_EVALUATION` is critical: predicates that
reference `count()` expressions depend on the current-tick timer value.

---

## What SIR does NOT do

- Does not replace the YAML format. YAML remains the authoring interface.
- Does not implement a Python FSM simulator. MATLAB simulation is the runtime;
  Python validation only checks structural/semantic correctness.
- Does not need to be serialized JSON. The SIR lives as Python dataclasses in
  memory. JSON serialization is optional and only needed if a cross-language
  boundary (e.g., a separate validator tool) is introduced.
- Does not need to extract every simple predicate. Wrapping `devOnline` in
  `P_devOnline` adds noise without benefit.

---

## YAML fields the SIR skeleton is missing

These must be handled in the normalization pass:

| YAML field | Where | SIR handling |
| ---------- | ----- | ------------ |
| `role:` | states | Preserve as layout annotation; not part of execution semantics |
| `subchart:` | states | Preserve; maps to `makeAtomic()` in codegen |
| `trigger:` | transitions | Separate field from `condition:`; maps to Stateflow event trigger |
| `locals:` | top-level | Keep distinct from `outputs:` and `inputs:` (affects data scope) |
| `order:` | transitions | Map to integer `priority`; **1 = highest priority** |

---

## What is implemented (`slxgen/stateflow_sir.py`)

Dataclasses: `SIRState`, `SIRTransition`, `SIRVariable`, `SIRModel`.

Functions:

| Function | Purpose |
| -------- | ------- |
| `yaml_to_sir(chart_dict)` | YAML nested dict → flat `SIRModel` (depth-first pre-order) |
| `sir_validate(sir)` | 7 structural checks; returns `list[str]` of issues |
| `sir_to_chart_dict(sir)` | Flat `SIRModel` → nested chart dict for `stateflow_dict_to_matlab()` |
| `sir_to_dict(sir, ...)` | Serialize to plain dict for JSON export |
| `sf_yaml_to_sir_json(yaml_path)` | Convenience: load YAML → SIR → validate → write JSON |
| `validate_and_report(chart_dict)` | Legacy wrapper (superseded by inline block in `stateflow.py`) |

Validator checks (see `sir_validate`):

| # | Severity | Check |
| - | -------- | ----- |
| 1 | ERROR | Transition `source` not in state set |
| 2 | ERROR | Transition `target` not in state set |
| 3 | WARNING | Transition missing `order` field |
| 4 | ERROR | Duplicate priority from same source state |
| 5 | WARNING | Multiple default substates in one parent |
| 6 | WARNING | Parent with children but no default substate |
| 7 | WARNING | Output or local variable has no `initial_value` |

---

## Reference schema fields

Compact field reference for each SIR object type. These are the fields that must
be supported by the Python dataclasses; not all are required at the minimal first
slice.

### SIRState

| Field | Type | Notes |
| ----- | ---- | ----- |
| `id` | `str` | Fully-qualified dotted path, e.g. `ACTIVE.STARTUP.CONNECTING` |
| `name` | `str` | Leaf name only, e.g. `CONNECTING` |
| `type` | `OR \| AND \| HISTORY` | Decomposition type; default OR |
| `parent` | `str \| None` | Dotted ID of parent state; None for root |
| `initial` | `bool` | True for the default substate within its parent |
| `actions.entry` | `list[str]` | MATLAB statements |
| `actions.during` | `list[str]` | MATLAB statements |
| `actions.exit` | `list[str]` | MATLAB statements |
| `timers.local` | `list[str]` | Timer IDs owned by this state; reset on entry |
| `invariants` | `list[str]` | Boolean expressions for testability; not enforced at runtime |
| `role` | `str \| None` | Layout annotation: `fault \| init \| main \| auxiliary` |
| `subchart` | `bool` | Maps to `makeAtomic()` in Stateflow API |
| `trace.req_ids` | `list[str]` | Requirement IDs (deferred — add when tooling chosen) |

### SIRTransition

| Field | Type | Notes |
| ----- | ---- | ----- |
| `id` | `str` | Auto-generated, e.g. `T_001` |
| `source` | `str` | Dotted state ID |
| `target` | `str` | Dotted state ID |
| `priority` | `int` | From YAML `order`; **1 = highest priority** |
| `condition` | `str \| None` | Inline expression or predicate ID reference |
| `trigger` | `str \| None` | Stateflow event trigger (e.g. `after(n,tick)`); distinct from condition |
| `action.type` | `NONE \| CONDITION \| TRANSITION` | CONDITION safe before junctions; TRANSITION for final segment only |
| `action.code` | `list[str]` | MATLAB statements |
| `trace.req_ids` | `list[str]` | Deferred |

### SIRPredicate

| Field | Type | Notes |
| ----- | ---- | ----- |
| `id` | `str` | `P_<name>` |
| `type` | `PURE \| TEMPORAL` | PURE = stateless logic; TEMPORAL = wraps a temporal operator |
| `scope` | `CHART \| STATE` | Where the predicate is evaluated |
| `definition.expression` | `str` | Full source expression (PURE type) |
| `temporal.mode` | `COUNT \| DURATION \| AFTER` | Must match the operator used in source |
| `temporal.signal_expr` | `str` | Boolean expression inside the temporal operator |
| `temporal.threshold` | `str` | Timeout value or variable name |
| `evaluation.frequency` | `EVERY_TICK` | Always; no per-event evaluation supported |
| `evaluation.caching` | `bool` | Memoize result within one cycle (safe for PURE only) |

### SIRTimer (explicit timer variables only)

Used when the YAML uses an explicit `timer += dt` pattern rather than a native
Stateflow temporal operator. Native `count()` / `duration()` / `after()` stay
inline in the predicate expression and do not produce a SIRTimer object.

| Field | Type | Notes |
| ----- | ---- | ----- |
| `id` | `str` | Variable name |
| `scope` | `STATE \| CHART` | Reset scope |
| `owner` | `str` | State ID whose during action performs the increment |
| `type` | `ACCUMULATING \| RESET_ON_ENTRY \| RESET_ON_CONDITION` | Reset behaviour |
| `dt_source` | `MODEL_DT` | Increment tied to model sample time |
| `reset_condition` | `str \| None` | Predicate ID or inline expression that triggers reset |
| `update_rule.enable_condition` | `str` | Condition under which the timer increments; `"true"` if always |
| `value_bounds.min` | `number` | Saturation floor |
| `value_bounds.max` | `number` | Saturation ceiling; required for safety-level models |

### SIRVariable

| Field | Type | Notes |
| ----- | ---- | ----- |
| `id` | `str` | Variable name |
| `type` | `str` | `boolean \| int32 \| single \| enum \| uint8 \| …` |
| `scope` | `INPUT \| OUTPUT \| LOCAL` | Maps to YAML `inputs:` / `outputs:` / `locals:` |
| `initial_value` | any | Required for OUTPUT and LOCAL; validator rejects if absent |
| `bounds.min` | number | Optional; required at Level 2+ |
| `bounds.max` | number | Optional; required at Level 2+ |
| `access` | `READ_WRITE \| READ_ONLY` | READ_ONLY for inputs |
| `trace.req_ids` | `list[str]` | Deferred |

### Traceability

Deferred until SIR schema is stabilised and requirement tooling is chosen.
Each SIR object carries a `trace.req_ids` list that is populated when the
source YAML includes requirement annotations.

---

## Roadmap

### Phase 3 — Second codegen target (Mermaid diagram) [next]

Generate a Mermaid state diagram directly from `SIRModel`. ~50 LOC, no impact on
the MATLAB path. Proves the "multiple outputs from one IR" value proposition with
minimal risk. Mermaid is preferable to `sir_to_matlab()` as the first multi-target
proof because it requires no geometry, no ELK integration, and no condition parsing.

Suggested output: `sf_yaml_to_mermaid(yaml_path) -> str` in `stateflow_sir.py`.

### Phase 4 — YAML normalization / auto-fix

Round-trip through SIR to canonicalize YAML files:

- Sort states into stable order
- Fill in missing `order` fields based on current list position
- Add `initial_value` defaults where flagged by the validator
- Emit clean YAML back with all fields explicit

Suggested entry point: `sir_normalize_yaml(yaml_path, output_path)`.

### Phase 5 — Predicate / timer extraction

Parse temporal conditions into typed SIR nodes (`SIRPredicate`, `SIRTimer`).

| Condition type | Treatment |
| -------------- | --------- |
| Simple signal: `devOnline`, `hasFault` | Keep inline — no extraction |
| Compound boolean: `devOnline && devStatus==X` | Extract only if reused |
| Temporal: `count(x)>t`, `duration(x)>t`, `after(n,tick)` | Always extract |

Inline temporal operators are valid Stateflow — `count(u>0)>timeout` is one
temporal predicate; do not decompose the inner expression further.

Once conditions are typed nodes rather than opaque strings:

- Detect temporal operator misuse (`duration()` in a discrete system)
- Auto-generate separate predicate MATLAB functions for reused conditions
- Export reachability / transition coverage data for formal analysis

The generation pipeline is unchanged: the SIR transformation produces a modified
`SIRModel`, `sir_to_chart_dict()` translates new node types into chart_dict
constructs that `stateflow_dict_to_matlab()` already understands (local variables,
`du:` action strings, inline conditions). No new codegen is needed.

### Architecture principle

All semantic work lives in SIR transforms. The codegen (`stateflow_dict_to_matlab`)
is frozen. The permanent pipeline is:

```text
YAML → yaml_to_sir() → [SIR transforms] → sir_to_chart_dict() → stateflow_dict_to_matlab()
```

`sir_to_matlab()` is **not planned** — the bridge architecture makes it unnecessary.
