"""Demonstrate SIR generation for Ex1_StMach_sf.yaml.

The Stateflow Intermediate Representation (SIR) is a normalized, validated
in-memory graph built from the YAML authoring format.  It sits between the
human-written YAML and the MATLAB codegen:

    YAML  -->  yaml_to_sir()  -->  SIRModel  -->  sir_validate()
                                                       |
                                               issues to stderr
                                                   (warnings)

The existing MATLAB codegen path (sf_yaml_to_matlab) is unchanged.
The SIR is currently used for validation only; a SIR-based codegen
(sir_to_matlab) is planned as a future phase.

This script shows three things:
  1. Load and normalize: YAML --> SIRModel
  2. Validate: report structural issues (missing order, bad state refs, etc.)
  3. Export: write the SIR to JSON for inspection / downstream tooling

Output: example/model_gen/generated/Ex1_StMach_sir.json
"""
from pathlib import Path
import sys
import json

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from slxgen.stateflow_sir import yaml_to_sir, sir_validate, sf_yaml_to_sir_json, sir_to_chart_dict

YAML    = Path(__file__).parent / 'Ex1_StMach_sf.yaml'
OUT_DIR = Path(__file__).parent / 'generated'
OUT_DIR.mkdir(exist_ok=True)
OUT_JSON = OUT_DIR / 'Ex1_StMach_sir.json'

# ---------------------------------------------------------------------------
# Step 1 — Load YAML and build SIR
# ---------------------------------------------------------------------------
# yaml_to_sir() accepts the same dict that goes to stateflow_dict_to_matlab().
# It returns a SIRModel with:
#   .states       — flat list of SIRState (one per state, parent-before-child)
#   .transitions  — flat list of SIRTransition
#   .variables    — inputs + outputs + locals, each tagged with .scope

import yaml
chart_dict = yaml.safe_load(YAML.read_text(encoding='utf-8'))
sir = yaml_to_sir(chart_dict)

print(f'SIR built from: {YAML.name}')
print(f'  States      : {len(sir.states)}')
print(f'  Transitions : {len(sir.transitions)}')
print(f'  Variables   : {len(sir.variables)}')
print()

# ---------------------------------------------------------------------------
# Step 2 — Inspect the normalised structure
# ---------------------------------------------------------------------------
# States are stored flat with explicit parent references and fully-qualified
# dotted IDs, matching the transition from/to syntax already in the YAML.

print('States (id -> parent):')
for s in sir.states:
    role_tag = f'  [{s.role}]' if s.role else ''
    default_tag = ' (default)' if s.initial else ''
    subchart_tag = ' [subchart]' if s.subchart else ''
    print(f'  {s.id:<45} parent={s.parent or "None"}{default_tag}{subchart_tag}{role_tag}')
print()

# Transitions carry integer priority (from YAML 'order'), separated trigger
# and condition fields, and an action_type tag.

print('Transitions (source -> target  priority  condition):')
for t in sir.transitions:
    prio = str(t.priority) if t.priority is not None else '?'
    cond = t.condition or ''
    trigger = f'  trigger={t.trigger}' if t.trigger else ''
    action = f'  action={t.action}' if t.action else ''
    print(f'  [{prio}] {t.source:<35} -> {t.target:<35} [{cond}]{trigger}{action}')
print()

# ---------------------------------------------------------------------------
# Step 3 — Validate
# ---------------------------------------------------------------------------
# sir_validate() checks:
#   ERROR   — source/target state not found, duplicate priority from same state
#   WARNING — missing 'order' field, multiple/missing defaults, no initial_value

issues = sir_validate(sir)
if issues:
    print(f'Validation issues ({len(issues)}):')
    for msg in issues:
        print(f'  {msg}')
else:
    print('Validation: clean (no issues)')
print()

# ---------------------------------------------------------------------------
# Step 4 — Export SIR to JSON
# ---------------------------------------------------------------------------
# sf_yaml_to_sir_json() is a convenience wrapper:
#   load YAML --> yaml_to_sir --> sir_validate --> JSON
# The JSON embeds the validation issues under 'validation.issues'.

sf_yaml_to_sir_json(YAML, output_path=OUT_JSON)
print(f'SIR JSON written: {OUT_JSON}')
size_kb = OUT_JSON.stat().st_size / 1024
print(f'  Size: {size_kb:.1f} KB')

# Quick spot-check: reload and verify round-trip
reloaded = json.loads(OUT_JSON.read_text(encoding='utf-8'))
assert reloaded['model']['name'] == sir.name
assert len(reloaded['states']) == len(sir.states)
assert len(reloaded['transitions']) == len(sir.transitions)
print('  Round-trip check: OK')

# ---------------------------------------------------------------------------
# Step 5 — Verify sir_to_chart_dict round-trip
# ---------------------------------------------------------------------------
# sir_to_chart_dict() converts the flat SIRModel back to the nested dict
# consumed by stateflow_dict_to_matlab(). This confirms that the SIR is now
# the authoritative source for generation, not just validation.

cd = sir_to_chart_dict(sir)
assert list(cd['states'].keys()) == list(chart_dict['states'].keys()), \
    f"top-level states mismatch: {list(cd['states'].keys())} vs {list(chart_dict['states'].keys())}"
assert len(cd['transitions']) == len(chart_dict['transitions']), \
    f"transition count mismatch: {len(cd['transitions'])} vs {len(chart_dict['transitions'])}"
assert cd['name'] == chart_dict['name']
print()
print('sir_to_chart_dict round-trip:')
print(f'  Top-level states : {list(cd["states"].keys())}')
print(f'  Transitions      : {len(cd["transitions"])}')
print('  Round-trip chart_dict: OK')
