"""Stateflow Intermediate Representation (SIR).

Normalization layer between the sf.yaml chart dict and MATLAB codegen.
Phase 1: dataclasses + yaml_to_sir() + sir_validate().
The existing stateflow_dict_to_matlab() codegen path is unchanged.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SIRVariable:
    name: str
    scope: str           # 'input' | 'output' | 'local'
    type: str | None
    initial_value: Any   # None if unspecified in YAML


@dataclass
class SIRState:
    id: str              # fully-qualified dotted path, e.g. 'ACTIVE.STARTUP'
    name: str            # leaf name only, e.g. 'STARTUP'
    parent: str | None   # dotted parent ID; None for root-level states
    initial: bool        # True = default substate within its parent
    decomp: str          # 'OR' | 'AND'
    subchart: bool
    role: str | None     # layout hint: 'fault' | 'init' | 'main' | 'auxiliary'
    en: str | None
    du: str | None
    ex: str | None


@dataclass
class SIRTransition:
    idx: int             # 0-based index in source list (for error messages)
    source: str          # dotted state ID
    target: str          # dotted state ID
    priority: int | None # from YAML 'order' (1 = highest); None if field absent
    condition: str | None
    trigger: str | None  # Stateflow event trigger, distinct from condition
    action: str | None
    action_type: str     # 'CONDITION' | 'TRANSITION' | 'NONE'


@dataclass
class SIRModel:
    name: str
    states: list[SIRState]
    transitions: list[SIRTransition]
    variables: list[SIRVariable]


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def yaml_to_sir(chart_dict: dict) -> SIRModel:
    """Normalise a chart_dict (as loaded from sf.yaml) into a SIRModel.

    The chart_dict is the same dict passed to stateflow_dict_to_matlab() —
    no changes to the caller are required.
    """
    name = chart_dict.get('name', 'unnamed')

    # --- Variables ---
    variables: list[SIRVariable] = []
    scope_map = {'inputs': 'input', 'outputs': 'output', 'locals': 'local'}
    for yaml_key, scope_label in scope_map.items():
        for v in chart_dict.get(yaml_key, []):
            variables.append(SIRVariable(
                name=v.get('name', ''),
                scope=scope_label,
                type=v.get('type'),
                initial_value=v.get('initial_value'),
            ))

    # --- States (recursive flatten, depth-first pre-order) ---
    states: list[SIRState] = []

    def _walk(states_dict: dict, parent_id: str | None) -> None:
        for state_name, state_data in states_dict.items():
            sid = f"{parent_id}.{state_name}" if parent_id else state_name
            states.append(SIRState(
                id=sid,
                name=state_name,
                parent=parent_id,
                initial=bool(state_data.get('default', False)),
                decomp='AND' if state_data.get('type') == 'AND' else 'OR',
                subchart=bool(state_data.get('subchart', False)),
                role=state_data.get('role'),
                en=state_data.get('en'),
                du=state_data.get('du'),
                ex=state_data.get('ex'),
            ))
            if 'states' in state_data:
                _walk(state_data['states'], sid)

    _walk(chart_dict.get('states', {}), None)

    # --- Transitions ---
    transitions: list[SIRTransition] = []
    for idx, t in enumerate(chart_dict.get('transitions', [])):
        order_raw = t.get('order')
        try:
            priority: int | None = int(order_raw) if order_raw is not None else None
        except (ValueError, TypeError):
            priority = None

        raw_action = t.get('action') or None
        if raw_action and raw_action.startswith('/'):
            action = raw_action[1:]   # strip sigil; value stored without '/'
            action_type = 'TRANSITION'
        elif raw_action:
            action = raw_action
            action_type = 'CONDITION'
        else:
            action = None
            action_type = 'NONE'
        transitions.append(SIRTransition(
            idx=idx,
            source=t.get('from', ''),
            target=t.get('to', ''),
            priority=priority,
            condition=t.get('condition') or None,
            trigger=t.get('trigger') or None,
            action=action,
            action_type=action_type,
        ))

    return SIRModel(name=name, states=states, transitions=transitions, variables=variables)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def sir_validate(sir: SIRModel) -> list[str]:
    """Validate a SIRModel. Returns a list of issue strings; empty = clean.

    Severities are embedded in each message: ERROR or WARNING.
    ERRORs indicate problems likely to produce wrong MATLAB output.
    WARNINGs indicate guideline violations or missing best-practice fields.
    """
    issues: list[str] = []
    state_ids = {s.id for s in sir.states}

    # --- Transition checks ---
    by_source: dict[str, list[SIRTransition]] = defaultdict(list)

    for t in sir.transitions:
        # Check 1 & 2: source/target state must exist
        if t.source not in state_ids:
            issues.append(
                f"ERROR: transition[{t.idx}] source '{t.source}' not found in states"
            )
        if t.target not in state_ids:
            issues.append(
                f"ERROR: transition[{t.idx}] target '{t.target}' not found in states"
            )

        # Check 3: missing order field
        if t.priority is None:
            issues.append(
                f"WARNING: transition[{t.idx}] {t.source} -> {t.target}: "
                f"no 'order' field (priority undefined)"
            )

        by_source[t.source].append(t)

    # Check 4: duplicate priority from same source
    for source, ts in by_source.items():
        seen: dict[int, SIRTransition] = {}
        for t in ts:
            if t.priority is None:
                continue
            if t.priority in seen:
                prev = seen[t.priority]
                issues.append(
                    f"ERROR: transitions[{prev.idx}] and [{t.idx}] from '{source}' "
                    f"share priority {t.priority}: execution order is ambiguous"
                )
            else:
                seen[t.priority] = t

    # --- State default checks ---
    children_by_parent: dict[str | None, list[SIRState]] = defaultdict(list)
    for s in sir.states:
        children_by_parent[s.parent].append(s)

    for parent_id, children in children_by_parent.items():
        defaults = [c for c in children if c.initial]
        label = f"'{parent_id}'" if parent_id else "root"

        # Check 5: multiple defaults
        if len(defaults) > 1:
            issues.append(
                f"WARNING: {label} has multiple default children: "
                f"{[d.name for d in defaults]}"
            )
        # Check 6: no default (when children exist — root level with one state is fine)
        elif len(defaults) == 0 and len(children) > 1:
            issues.append(
                f"WARNING: {label} has {len(children)} children but none is marked "
                f"default (first child '{children[0].name}' will be used)"
            )

    # --- Variable initialization checks ---
    # Check 7: output/local variables without initial_value
    for v in sir.variables:
        if v.scope in ('output', 'local') and v.initial_value is None:
            issues.append(
                f"WARNING: {v.scope} variable '{v.name}' has no initial_value "
                f"(uninitialized output risk)"
            )

    # --- Transition action checks ---
    # Build per-state maps for checks 8 and 9.
    state_by_id: dict[str, SIRState] = {s.id: s for s in sir.states}

    def _assigned_vars(code: str | None) -> set[str]:
        """Extract variable names from the left-hand side of assignments in a code block."""
        if not code:
            return set()
        import re as _re
        return set(_re.findall(r'\b([A-Za-z_]\w*)\s*=(?!=)', code))

    # Pre-compute per-state incoming transition index for check 9.
    # Maps target state id -> list of (transition, set-of-vars-assigned-in-action).
    incoming: dict[str, list[tuple]] = defaultdict(list)
    for t in sir.transitions:
        if t.target in state_by_id:
            incoming[t.target].append((t, _assigned_vars(t.action)))

    for t in sir.transitions:
        if not t.action or t.target not in state_by_id:
            continue
        t_vars = _assigned_vars(t.action)
        if not t_vars:
            continue
        target = state_by_id[t.target]

        # Check 8: transition action assigns a variable also in target en: — en: always wins
        en_vars = _assigned_vars(target.en)
        overlap = t_vars & en_vars
        if overlap:
            overlap_str = ', '.join(sorted(overlap))
            issues.append(
                f"WARNING: transition[{t.idx}] {t.source} -> {t.target}: "
                f"action assigns '{overlap_str}' which target en: also assigns "
                f"(en: overrides -- transition action may be redundant)"
            )

        # Check 9: output assigned inconsistently across entry paths.
        # Only fires when the target has multiple incoming transitions and at least
        # one of them does NOT assign the same output — meaning the output value
        # in the target state depends on which path was taken (genuine ambiguity).
        # Single-entry-path states are exempt: path-dependent assignment is unambiguous.
        # Pattern A (->fault role) is exempt: fault states intentionally latch fault
        # codes via transition action before the fault state resets other outputs.
        if target.role != 'fault':
            output_names = {v.name for v in sir.variables if v.scope == 'output'}
            all_incoming = incoming[t.target]
            if len(all_incoming) > 1:
                for out_var in (t_vars & output_names) - en_vars:
                    # Check if any other incoming transition omits this output
                    missing_paths = [
                        other_t.source for other_t, other_vars in all_incoming
                        if other_t.idx != t.idx and out_var not in other_vars
                    ]
                    if missing_paths:
                        issues.append(
                            f"WARNING: transition[{t.idx}] {t.source} -> {t.target}: "
                            f"output '{out_var}' set here but not on path(s) from "
                            f"{missing_paths} -- value in '{t.target}' depends on entry path"
                        )

    return issues


# ---------------------------------------------------------------------------
# Convenience: validate and print to stderr
# ---------------------------------------------------------------------------

def validate_and_report(chart_dict: dict, source_label: str = '') -> SIRModel:
    """Run yaml_to_sir + sir_validate, print issues to stderr, return the SIRModel."""
    sir = yaml_to_sir(chart_dict)
    issues = sir_validate(sir)
    if issues:
        prefix = f"[SIR:{source_label}] " if source_label else "[SIR] "
        for msg in issues:
            print(f"{prefix}{msg}", file=sys.stderr)
    return sir


# ---------------------------------------------------------------------------
# Round-trip: SIRModel -> chart_dict (nested, as consumed by stateflow_dict_to_matlab)
# ---------------------------------------------------------------------------

def sir_to_chart_dict(sir: SIRModel) -> dict:
    """Convert a flat SIRModel back to the nested chart_dict consumed by stateflow_dict_to_matlab().

    The output is structurally equivalent to the dict produced by yaml.safe_load() on
    the original sf.yaml, so the existing codegen and ELK layout functions receive
    exactly what they expect.
    """
    # --- Variables: split by scope ---
    scope_keys = {'input': 'inputs', 'output': 'outputs', 'local': 'locals'}
    var_lists: dict[str, list] = {'inputs': [], 'outputs': [], 'locals': []}
    for v in sir.variables:
        entry: dict = {'name': v.name}
        if v.type is not None:
            entry['type'] = v.type
        if v.initial_value is not None:
            entry['initial_value'] = v.initial_value
        var_lists[scope_keys[v.scope]].append(entry)

    # --- States: flat list -> nested dict ---
    # nodes_data maps id -> the mutable state dict inserted into the tree
    nodes_data: dict[str, dict] = {}
    top_states: dict[str, dict] = {}

    for s in sir.states:
        node: dict = {}
        if s.initial:
            node['default'] = True
        if s.decomp == 'AND':
            node['type'] = 'AND'
        if s.subchart:
            node['subchart'] = True
        if s.role is not None:
            node['role'] = s.role
        if s.en is not None:
            node['en'] = s.en
        if s.du is not None:
            node['du'] = s.du
        if s.ex is not None:
            node['ex'] = s.ex

        nodes_data[s.id] = node

        if s.parent is None:
            top_states[s.name] = node
        else:
            parent_node = nodes_data[s.parent]
            if 'states' not in parent_node:
                parent_node['states'] = {}
            parent_node['states'][s.name] = node

    # --- Transitions ---
    transitions = []
    for t in sir.transitions:
        entry: dict = {'from': t.source, 'to': t.target}
        if t.priority is not None:
            entry['order'] = str(t.priority)
        if t.condition is not None:
            entry['condition'] = t.condition
        if t.trigger is not None:
            entry['trigger'] = t.trigger
        if t.action is not None:
            # Re-add '/' sigil for transition actions so _rebuild_transition_label
            # can distinguish them from condition actions.
            entry['action'] = f'/{t.action}' if t.action_type == 'TRANSITION' else t.action
        transitions.append(entry)

    cd: dict = {'name': sir.name}
    for key in ('inputs', 'outputs', 'locals'):
        if var_lists[key]:
            cd[key] = var_lists[key]
    cd['states'] = top_states
    cd['transitions'] = transitions
    return cd


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------

_SIR_VERSION = "0.1.0"


def sir_to_dict(sir: SIRModel, source: str = '', issues: list[str] | None = None) -> dict:
    """Serialize a SIRModel to a plain dict suitable for JSON export."""
    d = asdict(sir)
    return {
        "sir_version": _SIR_VERSION,
        "source": source,
        "model": {"name": sir.name},
        "states": d["states"],
        "transitions": d["transitions"],
        "variables": d["variables"],
        "validation": {
            "issue_count": len(issues) if issues else 0,
            "issues": issues or [],
        },
    }


def sf_yaml_to_sir_json(yaml_path: str | Path, output_path: str | Path | None = None,
                        indent: int = 2) -> str:
    """Load an sf.yaml file, run SIR normalization + validation, return JSON string.

    If output_path is given, also writes the JSON to disk.
    Validation issues are embedded in the JSON under 'validation.issues' and also
    printed to stderr.
    """
    import yaml as _yaml  # local import to avoid circular deps if used standalone

    yaml_path = Path(yaml_path)
    chart_dict = _yaml.safe_load(yaml_path.read_text(encoding='utf-8'))
    sir = yaml_to_sir(chart_dict)
    issues = sir_validate(sir)

    if issues:
        for msg in issues:
            print(f"[SIR:{yaml_path.name}] {msg}", file=sys.stderr)

    payload = sir_to_dict(sir, source=yaml_path.name, issues=issues)
    text = json.dumps(payload, indent=indent, default=str)

    if output_path:
        Path(output_path).write_text(text, encoding='utf-8')

    return text
