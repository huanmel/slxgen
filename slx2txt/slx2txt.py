import xml.etree.ElementTree as ET
import json
import re
import yaml
import zipfile
from pathlib import Path
from typing import Dict, List, Any
from collections import defaultdict


# ----------------------------------------------------------------------
# 1. Parsing – build port maps for Inport / Outport blocks
# ----------------------------------------------------------------------
def _build_port_map(blocks: Dict[int, Dict]) -> tuple[Dict[int, str], Dict[int, str]]:
    """Return (input_port_map, output_port_map) – port_number → name."""
    in_map: Dict[int, str] = {}
    out_map: Dict[int, str] = {}

    # collect all Inport / Outport blocks
    inports = [b for b in blocks.values() if b['type'] == 'Inport']
    outports = [b for b in blocks.values() if b['type'] == 'Outport']

    # ----- INPUT ports -------------------------------------------------
    used = set()
    # 1) explicit Port parameter
    for blk in inports:
        p = blk['parameters'].get('Port')
        if p:
            num = int(p)
            if num not in used:
                in_map[num] = blk['name']
                used.add(num)
    # 2) fill gaps with default numbering
    next_num = 1
    for blk in inports:
        if 'Port' not in blk['parameters']:
            while next_num in used:
                next_num += 1
            in_map[next_num] = blk['name']
            used.add(next_num)
            next_num += 1

    # ----- OUTPUT ports ------------------------------------------------
    used.clear()
    for blk in outports:
        p = blk['parameters'].get('Port')
        if p:
            num = int(p)
            if num not in used:
                out_map[num] = blk['name']
                used.add(num)
    next_num = 1
    for blk in outports:
        if 'Port' not in blk['parameters']:
            while next_num in used:
                next_num += 1
            out_map[next_num] = blk['name']
            used.add(next_num)
            next_num += 1

    return in_map, out_map


def load_stateflow_machine(z) -> Dict[str, int]:
    """Parse simulink/stateflow/machine.xml -> {instance_name: chart_id}."""
    path = 'simulink/stateflow/machine.xml'
    if path not in z.namelist():
        return {}
    root = ET.fromstring(z.read(path).decode('utf-8'))
    result = {}
    for inst in root.findall('.//instance'):
        name = inst.findtext("P[@Name='name']")
        chart = inst.findtext("P[@Name='chart']")
        if name and chart:
            result[name] = int(chart)
    return result


def _collect_sf_states(children_elem, parent_path: str, depth: int,
                        ssid_to_path: Dict[str, str], states: list) -> None:
    if children_elem is None:
        return
    for state in children_elem.findall('state'):
        ssid  = state.get('SSID', '')
        label = state.findtext("P[@Name='labelString']", '').strip()
        lines = label.split('\n')
        sname = lines[0].strip()
        actions = '\n'.join(l.strip() for l in lines[1:] if l.strip())
        full_path = f'{parent_path}.{sname}' if parent_path else sname
        ssid_to_path[ssid] = full_path
        is_subchart = state.findtext("P[@Name='superState']", '') == 'SUBCHART'
        states.append({
            'ssid': ssid, 'name': sname, 'path': full_path,
            'depth': depth, 'actions': actions, 'is_default': False,
            'state_type': state.findtext("P[@Name='type']", 'OR_STATE'),
            'is_subchart': is_subchart,
        })
        _collect_sf_states(state.find('Children'), full_path, depth + 1, ssid_to_path, states)


def _parse_transition_label(label: str) -> Dict[str, str]:
    """Split a Stateflow transition label into trigger / condition / action.

    Full format: trigger[condition]{action}
    Examples:
      '[cond]'                    → trigger='', condition='cond', action=''
      '[cond]{act}'               → trigger='', condition='cond', action='act'
      'after(N,tick)[cond]{act}'  → trigger='after(N,tick)', condition='cond', action='act'
      'evtName'                   → trigger='evtName', condition='', action=''
    """
    trigger, condition, action = '', '', ''
    m = re.match(r'^(.*?)\s*\[([^\]]*)\](?:\s*\{([^}]*)\})?$', label, re.DOTALL)
    if m:
        trigger   = m.group(1).strip()
        condition = m.group(2).strip()
        action    = (m.group(3) or '').strip()
    else:
        m2 = re.match(r'^(.*?)\s*\{([^}]*)\}$', label, re.DOTALL)
        if m2:
            trigger = m2.group(1).strip()
            action  = m2.group(2).strip()
        else:
            trigger = label.strip()
    return {'trigger': trigger, 'condition': condition, 'action': action}


def parse_stateflow_chart(z, chart_id: int) -> Dict:
    """Parse simulink/stateflow/chart_{id}.xml into a structured dict."""
    chart_path = f'simulink/stateflow/chart_{chart_id}.xml'
    if chart_path not in z.namelist():
        return {}
    root = ET.fromstring(z.read(chart_path).decode('utf-8'))

    # inputs / outputs / locals
    inputs, outputs, locals_ = [], [], []
    for d in root.findall('.//data'):
        dname = d.get('name', '')
        scope = d.findtext("P[@Name='scope']", '')
        dtype = d.findtext("P[@Name='dataType']", '')
        if scope == 'INPUT_DATA':
            inputs.append({'name': dname, 'type': dtype})
        elif scope == 'OUTPUT_DATA':
            outputs.append({'name': dname, 'type': dtype})
        elif scope == 'LOCAL_DATA':
            locals_.append({'name': dname, 'type': dtype})

    # states – recursive traversal, building full dotted paths
    ssid_to_path: Dict[str, str] = {}
    states: list = []
    _collect_sf_states(root.find('Children'), '', 0, ssid_to_path, states)

    # default states: entry transitions have no src SSID — find ALL of them
    # (one per container state/chart, not just the first)
    for t in root.findall('.//transition'):
        if t.findtext("src/P[@Name='SSID']") is None:
            dst_ssid = t.findtext("dst/P[@Name='SSID']")
            if dst_ssid:
                for s in states:
                    if s['ssid'] == dst_ssid:
                        s['is_default'] = True
                        break

    # transitions (skip default entry)
    transitions = []
    for t in root.findall('.//transition'):
        src_ssid = t.findtext("src/P[@Name='SSID']")
        dst_ssid = t.findtext("dst/P[@Name='SSID']")
        if not src_ssid or not dst_ssid:
            continue
        label  = t.findtext("P[@Name='labelString']", '').strip()
        parsed = _parse_transition_label(label)
        order  = t.findtext("P[@Name='executionOrder']", '')
        transitions.append({
            'src':       ssid_to_path.get(src_ssid, src_ssid),
            'dst':       ssid_to_path.get(dst_ssid, dst_ssid),
            'trigger':   parsed['trigger'],
            'condition': parsed['condition'],
            'action':    parsed['action'],
            'order':     order,
        })

    return {
        'name': root.findtext("P[@Name='name']", ''),
        'inputs': inputs,
        'outputs': outputs,
        'locals': locals_,
        'states': states,
        'transitions': transitions,
    }


_SF_KW_RE = re.compile(r'^(en|du|ex|en,du|du,ex|en,ex|en,du,ex)\s*:(.*)', re.IGNORECASE)


def _render_sf_actions(actions: str, kw_indent: str, code_indent: str) -> list:
    result = []
    for line in actions.split('\n'):
        stripped = line.strip()
        if not stripped:
            continue
        m = _SF_KW_RE.match(stripped)
        if m:
            result.append(f'{kw_indent}{m.group(1)}:')
            if m.group(2).strip():
                result.append(f'{code_indent}{m.group(2).strip()}')
        else:
            result.append(f'{code_indent}{stripped}')
    return result


def _sf_render(sf: Dict, path: str) -> str:
    """Render a parsed stateflow chart dict as LLM-readable text."""
    lines = [f"\n=== {path} [Stateflow] ==="]
    if sf.get('inputs'):
        lines.append('INPUTS:  ' + ' | '.join(d['name'] for d in sf['inputs']))
    if sf.get('outputs'):
        lines.append('OUTPUTS: ' + ' | '.join(d['name'] for d in sf['outputs']))
    lines.append('')
    lines.append('STATES:')
    for s in sf.get('states', []):
        indent = '  ' * (s.get('depth', 0) + 1)
        tags = []
        if s.get('state_type') == 'AND_STATE':
            tags.append('[AND]')
        if s.get('is_subchart'):
            tags.append('[SUBCHART]')
        tag = ('  ' + ' '.join(tags)) if tags else ''
        ann = f"  (default, {s['path']})" if s.get('is_default') else f"  ({s['path']})"
        lines.append(f"{indent}{s['name']}{tag}{ann}")
        lines.extend(_render_sf_actions(s.get('actions', ''), indent + '  ', indent + '    '))
    lines.append('')
    lines.append('TRANSITIONS:')
    max_src = max((len(t['src']) for t in sf.get('transitions', [])), default=0)
    for t in sf.get('transitions', []):
        trig  = f"{t['trigger']} " if t.get('trigger') else ''
        cond  = f"[{t['condition']}]" if t['condition'] else ''
        act   = f"{{{t['action']}}}" if t.get('action') else ''
        arrow = f"{trig}--{cond}{act}-->"
        lines.append(f"  {t['src']:<{max_src}}  {arrow}  {t['dst']}")
    return '\n'.join(lines)


# ----------------------------------------------------------------------
# Stateflow export: flat internal dict → clean nested YAML/JSON structure
# ----------------------------------------------------------------------

class _SFYamlDumper(yaml.Dumper):
    """YAML dumper that renders multi-line strings as literal block scalars (|)."""

_SFYamlDumper.add_representer(
    str,
    lambda dumper, data: dumper.represent_scalar(
        'tag:yaml.org,2002:str', data, style='|' if '\n' in data else None
    )
)


def _parse_sf_actions_dict(actions: str) -> Dict[str, str]:
    """Parse 'en:code\ndu:code' action string into {'en': 'code', 'du': 'code', ...}."""
    result: Dict[str, str] = {}
    current_kw = None
    current_lines: list = []

    def _flush():
        if current_kw:
            result[current_kw] = '\n'.join(current_lines).strip()

    for line in actions.split('\n'):
        stripped = line.strip()
        if not stripped:
            continue
        m = _SF_KW_RE.match(stripped)
        if m:
            _flush()
            current_kw = m.group(1).lower()
            current_lines = [m.group(2).strip()] if m.group(2).strip() else []
        else:
            current_lines.append(stripped)
    _flush()
    return result


def _sf_states_to_nested(states: list) -> Dict:
    """Convert flat state list (with dotted paths) into a nested ordered dict."""
    tree: Dict = {}
    # sort by depth to ensure parents inserted before children
    for s in sorted(states, key=lambda x: x.get('depth', 0)):
        parts = s['path'].split('.')
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {}).setdefault('states', {})
        leaf = node.setdefault(parts[-1], {})
        if s.get('is_default'):
            leaf['default'] = True
        if s.get('is_subchart'):
            leaf['subchart'] = True
        if s.get('state_type') == 'AND_STATE':
            leaf['type'] = 'AND'
        actions = _parse_sf_actions_dict(s.get('actions', ''))
        leaf.update(actions)
    return tree


def stateflow_chart_to_dict(sf: Dict) -> Dict:
    """Convert internal stateflow parse result to a clean, portable nested dict.

    Suitable for YAML/JSON export and later re-import to recreate the chart.
    Schema:
      name        : chart name
      inputs      : [{name, type}]
      outputs     : [{name, type}]
      states      : nested dict — each state: {default?, type?, en?, du?, ex?, states?}
      transitions : [{from, to, trigger?, condition?, action?, order?}]
    """
    transitions = []
    for t in sf.get('transitions', []):
        entry: Dict = {'from': t['src'], 'to': t['dst']}
        if t.get('trigger'):
            entry['trigger'] = t['trigger']
        if t.get('condition'):
            entry['condition'] = t['condition']
        if t.get('action'):
            entry['action'] = t['action']
        if t.get('order'):
            entry['order'] = t['order']
        transitions.append(entry)

    return {
        'name':        sf.get('name', ''),
        'inputs':      sf.get('inputs', []),
        'outputs':     sf.get('outputs', []),
        'locals':      sf.get('locals', []),
        'states':      _sf_states_to_nested(sf.get('states', [])),
        'transitions': transitions,
    }


def _collect_sf_charts(slim: Dict) -> Dict[str, Dict]:
    """Recursively collect all stateflow charts from a slim model dict.
    Returns {block_name: stateflow_parse_dict}.
    """
    charts: Dict[str, Dict] = {}
    for blk in slim.get('blocks', {}).values():
        sf = blk.get('stateflow')
        if sf:
            charts[blk.get('name', 'chart')] = sf
        if 'subsystem' in blk:
            charts.update(_collect_sf_charts(blk['subsystem']))
    return charts


# ----------------------------------------------------------------------
# Stateflow → MATLAB script generation
# ----------------------------------------------------------------------

# Layout constants (Stateflow pixels)
_SF_LEAF_W   = 150   # minimum width of a leaf state
_SF_LEAF_H   = 80    # minimum height of a leaf state
_SF_HEADER_H = 30    # top strip reserved for state name label inside a parent
_SF_PADDING  = 20    # inner padding between parent edge and children
_SF_GAP      = 20    # gap between sibling states
_SF_MAX_COLS = 4     # max columns before wrapping to a new row


def _sf_state_size(state_body: Dict) -> tuple:
    """Return (width, height) required to render this state and all its children."""
    import math
    children = state_body.get('states', {})
    if not children:
        return (_SF_LEAF_W, _SF_LEAF_H)

    names = list(children.keys())
    n = len(names)
    cols = min(n, _SF_MAX_COLS)
    rows = math.ceil(n / cols)

    sizes = [_sf_state_size(children[name]) for name in names]

    col_w = [
        max(sizes[r * cols + c][0] for r in range(rows) if r * cols + c < n)
        for c in range(cols)
    ]
    row_h = [
        max(sizes[r * cols + c][1] for c in range(cols) if r * cols + c < n)
        for r in range(rows)
    ]

    total_w = 2 * _SF_PADDING + sum(col_w) + (cols - 1) * _SF_GAP
    total_h = _SF_HEADER_H + 2 * _SF_PADDING + sum(row_h) + (rows - 1) * _SF_GAP
    return (total_w, total_h)


def _compute_sf_layout(states_dict: Dict, origin_x: int = 20, origin_y: int = 20,
                        path_prefix: str = '') -> Dict[str, tuple]:
    """Recursively compute {dotted.path: (x, y, w, h)} for all states.

    Positions are in the parent container's local coordinate space.
    Root states are laid out left-to-right starting at (origin_x, origin_y).
    """
    import math
    result: Dict[str, tuple] = {}
    names = list(states_dict.keys())
    n = len(names)
    if n == 0:
        return result

    cols = min(n, _SF_MAX_COLS)
    rows = math.ceil(n / cols)
    sizes = {name: _sf_state_size(states_dict[name]) for name in names}

    col_w = [
        max(sizes[names[r * cols + c]][0] for r in range(rows) if r * cols + c < n)
        for c in range(cols)
    ]
    row_h = [
        max(sizes[names[r * cols + c]][1] for c in range(cols) if r * cols + c < n)
        for r in range(rows)
    ]

    for idx, name in enumerate(names):
        r, c = divmod(idx, cols)
        x = origin_x + sum(col_w[:c]) + c * _SF_GAP
        y = origin_y + sum(row_h[:r]) + r * _SF_GAP
        w, h = sizes[name]
        full_path = f'{path_prefix}.{name}' if path_prefix else name
        result[full_path] = (x, y, w, h)
        children = states_dict[name].get('states', {})
        if children:
            result.update(_compute_sf_layout(
                children,
                origin_x=x + _SF_PADDING,
                origin_y=y + _SF_HEADER_H + _SF_PADDING,
                path_prefix=full_path,
            ))

    return result


def _rebuild_state_label(name: str, actions: Dict[str, str]) -> str:
    """Reconstruct the Stateflow LabelString for a state from its name and actions dict."""
    parts = [name]
    for kw in ('en', 'du', 'ex'):
        code = actions.get(kw, '').strip()
        if code:
            parts.append(f'{kw}:\n{code}')
    for kw, code in actions.items():
        if kw not in ('en', 'du', 'ex') and code.strip():
            parts.append(f'{kw}:\n{code.strip()}')
    return '\n'.join(parts)


def _rebuild_transition_label(trigger: str, condition: str, action: str) -> str:
    """Reconstruct a Stateflow transition LabelString from parsed fields."""
    parts = []
    if trigger:
        parts.append(trigger)
    if condition:
        parts.append(f'[{condition}]')
    if action:
        parts.append(f'{{{action}}}')
    return ''.join(parts)


def _escape_matlab_str(s: str) -> str:
    """Escape a string for use inside MATLAB single-quoted string literals."""
    return s.replace("'", "''")


def _matlab_str_literal(s: str) -> str:
    """Return a MATLAB expression for string s.

    Multi-line strings use sprintf('...\\n...') so MATLAB parses them correctly.
    Single-line strings use plain 'value' notation.
    """
    escaped = _escape_matlab_str(s)
    if '\n' in escaped:
        # Replace actual newlines with \n escape sequences inside sprintf
        return "sprintf('" + escaped.replace('\n', '\\n') + "')"
    return f"'{escaped}'"


def _lca_path(path1: str, path2: str) -> str:
    """Return the dotted path of the lowest common ancestor of two state paths.

    Returns '' when the LCA is the chart itself (i.e. both are top-level states).
    """
    if not path1 or not path2:
        return ''
    parts1 = path1.split('.')
    parts2 = path2.split('.')
    common = []
    for a, b in zip(parts1, parts2):
        if a == b:
            common.append(a)
        else:
            break
    return '.'.join(common)


def _emit_sf_default_transition(
    dst_var: str,
    parent_var: str,
    counter: List[int],
    lines: List[str],
    is_auto: bool = False,
) -> None:
    """Emit a Stateflow default transition (no source) pointing to dst_var.

    Uses Stateflow.Transition(parent_var) with Source left unset — the correct
    API pattern for default transitions, which avoids junction clutter and keeps
    the transition inside its natural parent container.
    """
    counter[0] += 1
    tv = f't{counter[0]}'
    if is_auto:
        lines.append('% AUTO-DEFAULT (no default child in YAML; using first child)')
    lines.append(f"{tv} = Stateflow.Transition({parent_var});")
    lines.append(f"{tv}.Destination = {dst_var};")
    lines.append(f"{tv}.DestinationOClock = 0;")
    lines.append(f"{tv}.SourceEndPoint = {tv}.DestinationEndpoint + [0 -30];")
    lines.append(f"{tv}.MidPoint = {tv}.DestinationEndpoint + [0 -15];")


def _sf_states_to_matlab_lines(
    states_dict: Dict,
    parent_var: str,
    path_prefix: str,
    counter: List[int],
    path_to_var: Dict[str, str],
    lines: List[str],
    positions: Dict[str, tuple],
) -> None:
    """Recursively emit MATLAB lines that create Stateflow states."""
    # Identify the default child: explicit first, then auto-fallback to first entry
    default_child_name = None
    has_explicit_default = False
    for name, body in states_dict.items():
        if body.get('default'):
            default_child_name = name
            has_explicit_default = True
            break
    if default_child_name is None and states_dict:
        default_child_name = next(iter(states_dict))

    for state_name, state_body in states_dict.items():
        counter[0] += 1
        var = f's{counter[0]}'
        full_path = f'{path_prefix}.{state_name}' if path_prefix else state_name
        path_to_var[full_path] = var

        actions = {k: v for k, v in state_body.items()
                   if k not in ('states', 'default', 'type', 'subchart') and isinstance(v, str)}
        label = _rebuild_state_label(state_name, actions)

        lines.append(f"{var} = Stateflow.State({parent_var});")
        lines.append(f"{var}.Name = '{_escape_matlab_str(state_name)}';")
        lines.append(f"{var}.LabelString = {_matlab_str_literal(label)};")
        if full_path in positions:
            x, y, w, h = positions[full_path]
            lines.append(f"{var}.Position = [{x} {y} {w} {h}];")

        if state_body.get('subchart'):
            lines.append(f"{var}.IsSubchart = true;")

        if state_body.get('type') == 'AND':
            lines.append(f"{var}.Decomposition = 'PARALLEL_AND';")

        if state_name == default_child_name:
            _emit_sf_default_transition(
                var, parent_var, counter, lines,
                is_auto=not has_explicit_default,
            )

        children = state_body.get('states', {})
        if children:
            _sf_states_to_matlab_lines(children, var, full_path, counter, path_to_var, lines, positions)


def stateflow_dict_to_matlab(chart_dict: Dict, model_name: str = None) -> str:
    """Generate a MATLAB .m script that recreates a Stateflow chart from a chart dict.

    chart_dict should be the output of stateflow_chart_to_dict().
    model_name defaults to the chart name with spaces replaced by underscores.
    """
    chart_name = chart_dict.get('name', 'Chart')
    if model_name is None:
        model_name = re.sub(r'[^\w]', '_', chart_name)

    lines: List[str] = []
    lines.append(f'%% Generated by slx2txt — recreates Stateflow chart: {chart_name}')
    lines.append('')
    lines.append(f"model_name = '{_escape_matlab_str(model_name)}';")
    lines.append("if bdIsLoaded(model_name), close_system(model_name, 0); end")
    lines.append("if exist([model_name '.slx'], 'file'), delete([model_name '.slx']); end")
    lines.append("new_system(model_name);")
    lines.append("load_system(model_name);")
    lines.append('')
    lines.append(f"add_block('sflib/Chart', [model_name '/Chart']);")
    lines.append("rt = sfroot;")
    lines.append("m = rt.find('-isa', 'Stateflow.Machine', 'Name', model_name);")
    lines.append("ch = m.find('-isa', 'Stateflow.Chart');")
    lines.append(f"ch.Name = '{_escape_matlab_str(chart_name)}';")

    # Inputs
    inputs = chart_dict.get('inputs', [])
    if inputs:
        lines.append('')
        lines.append('%% Inputs')
    for i, d in enumerate(inputs, 1):
        v = f'd_in{i}'
        lines.append(f"{v} = Stateflow.Data(ch);")
        lines.append(f"{v}.Name = '{_escape_matlab_str(d['name'])}';")
        lines.append(f"{v}.Scope = 'Input';")
        if d.get('type'):
            lines.append(f"{v}.DataType = '{_escape_matlab_str(d['type'])}';")

    # Outputs
    outputs = chart_dict.get('outputs', [])
    if outputs:
        lines.append('')
        lines.append('%% Outputs')
    for i, d in enumerate(outputs, 1):
        v = f'd_out{i}'
        lines.append(f"{v} = Stateflow.Data(ch);")
        lines.append(f"{v}.Name = '{_escape_matlab_str(d['name'])}';")
        lines.append(f"{v}.Scope = 'Output';")
        if d.get('type'):
            lines.append(f"{v}.DataType = '{_escape_matlab_str(d['type'])}';")

    # Locals
    locals_ = chart_dict.get('locals', [])
    if locals_:
        lines.append('')
        lines.append('%% Local variables')
    for i, d in enumerate(locals_, 1):
        v = f'd_loc{i}'
        lines.append(f"{v} = Stateflow.Data(ch);")
        lines.append(f"{v}.Name = '{_escape_matlab_str(d['name'])}';")
        lines.append(f"{v}.Scope = 'Local';")
        if d.get('type'):
            lines.append(f"{v}.DataType = '{_escape_matlab_str(d['type'])}';")

    # States
    states_dict = chart_dict.get('states', {})
    counter: List[int] = [0]
    path_to_var: Dict[str, str] = {}
    positions: Dict[str, tuple] = {}
    if states_dict:
        positions = _compute_sf_layout(states_dict)
        lines.append('')
        lines.append('%% States')
        _sf_states_to_matlab_lines(states_dict, 'ch', '', counter, path_to_var, lines, positions)

    # Transitions
    transitions = chart_dict.get('transitions', [])
    if transitions:
        lines.append('')
        lines.append('%% Transitions')
    for tr in transitions:
        counter[0] += 1
        tv = f't{counter[0]}'
        src_path = tr.get('from', '')
        dst_path = tr.get('to', '')
        src_var = path_to_var.get(src_path, '')
        dst_var = path_to_var.get(dst_path, '')
        lca = _lca_path(src_path, dst_path)
        tr_parent_var = path_to_var.get(lca, 'ch') if lca else 'ch'
        label = _rebuild_transition_label(
            tr.get('trigger', ''), tr.get('condition', ''), tr.get('action', '')
        )
        lines.append(f"{tv} = Stateflow.Transition({tr_parent_var});")
        if src_var:
            lines.append(f"{tv}.Source = {src_var};")
        else:
            lines.append(f"% WARNING: source state '{src_path}' not found")
        if dst_var:
            lines.append(f"{tv}.Destination = {dst_var};")
        else:
            lines.append(f"% WARNING: destination state '{dst_path}' not found")
        if label:
            lines.append(f"{tv}.LabelString = {_matlab_str_literal(label)};")
        # Set explicit endpoint clock positions and midpoint so MATLAB keeps
        # the arc inside its natural parent (LCA).  Without this, MATLAB's
        # default arc routing for backward transitions goes above the states,
        # often escaping the enclosing state boundary.
        if src_path in positions and dst_path in positions:
            sx, sy, sw, sh = positions[src_path]
            dx, dy, dw, dh = positions[dst_path]
            src_cx = sx + sw // 2
            dst_cx = dx + dw // 2
            mid_x  = (src_cx + dst_cx) // 2
            mid_y  = ((sy + sh // 2) + (dy + dh // 2)) // 2
            lines.append(f"{tv}.MidPoint = [{mid_x} {mid_y}];")
            if src_cx <= dst_cx:   # forward (left → right)
                lines.append(f"{tv}.SourceOClock = 3;")
                lines.append(f"{tv}.DestinationOClock = 9;")
            else:                  # backward (right → left) — would arc upward by default
                lines.append(f"{tv}.SourceOClock = 9;")
                lines.append(f"{tv}.DestinationOClock = 3;")

    lines.append('')
    lines.append('% Auto-arrange blocks in the Simulink diagram')
    lines.append("Simulink.BlockDiagram.arrangeSystem(model_name);")
    lines.append('% Note: internal Stateflow state layout must be arranged manually')
    lines.append('% (open the chart and use Format > Auto Arrange)')
    lines.append('save_system(model_name);')
    lines.append(f"disp(['Chart saved to model: ' model_name]);")
    lines.append('')
    lines.append('%% Diagnostics — compile the diagram to surface Stateflow errors')
    lines.append('try')
    lines.append("    set_param(model_name, 'SimulationCommand', 'update');")
    lines.append("    disp('Diagram update: OK');")
    lines.append('catch e')
    lines.append("    fprintf('Diagram update errors:\\n%s\\n', e.message);")
    lines.append('end')

    return '\n'.join(lines) + '\n'


def sf_yaml_to_matlab(yaml_path, output_path=None) -> str:
    """Read a Stateflow sf.yaml file and generate a MATLAB script to recreate it.

    Returns the script as a string. If output_path is given, also writes it to disk.
    """
    chart_dict = yaml.safe_load(Path(yaml_path).read_text(encoding='utf-8'))
    script = stateflow_dict_to_matlab(chart_dict)
    if output_path:
        Path(output_path).write_text(script, encoding='utf-8')
    return script


def parse_system(system_elem, z, system_ref, rels_dir='simulink/systems/_rels', sf_machine=None, sf_path=''):
    # ---- .rels -------------------------------------------------------
    rels_path = f'{rels_dir}/{system_ref}.xml.rels'
    rels = {}
    if rels_path in z.namelist():
        rels_xml = z.read(rels_path).decode('utf-8')
        rels_root = ET.fromstring(rels_xml)
        ns = {'rel': 'http://schemas.openxmlformats.org/package/2006/relationships'}
        for rel in rels_root.findall('rel:Relationship', ns):
            rels[rel.get('Id')] = rel.get('Target')

    # ---- blocks -------------------------------------------------------
    blocks: Dict[str, Dict[str, Any]] = {}
    for block in system_elem.findall('./Block'):
        sid = block.get('SID').strip()
        blk = {
            'sid': sid,
            'name': block.get('Name'),
            'type': block.get('BlockType'),
            'parameters': {p.get('Name'): p.text for p in block.findall('./P') if p.get('Name')},
            'instance_data': {p.get('Name'): p.text for p in block.findall('./InstanceData/P') if p.get('Name')},
            'inputs': defaultdict(lambda: defaultdict(list)),   # port → {src_sid: [src_port,...]}
            'outputs': defaultdict(lambda: defaultdict(list)),  # port → {dst_sid: [dst_port,...]}
            'connection': {'incoming': [], 'outgoing': []},
        }

        # ---- bus element port renaming (BusElementIn* → PortName.Element) --
        if blk['type'] in ('Inport', 'Outport'):
            iface = block.find("./List[@ListType='InterfaceData']")
            if iface is not None:
                iface_data = {p.get('Name'): p.text for p in iface.findall('./P') if p.get('Name')}
                port_name = iface_data.get('PortName')
                element   = iface_data.get('Element')
                if port_name and element:
                    blk['name'] = f'{port_name}.{element}'

        # ---- stateflow chart -------------------------------------------
        if blk['parameters'].get('SFBlockType') == 'Chart' and sf_machine:
            blk_sf_key = f"{sf_path}/{blk['name']}" if sf_path else blk['name']
            chart_id = sf_machine.get(blk_sf_key)
            if chart_id is not None:
                blk['stateflow'] = parse_stateflow_chart(z, chart_id)

        # ---- subsystem -------------------------------------------------
        sub = block.find('./System')
        if sub is not None:
            sub_ref = sub.get('Ref')
            target = rels.get(sub_ref, f'{sub_ref}.xml')
            sub_path = f'simulink/systems/{target}'
            child_sf_path = f"{sf_path}/{blk['name']}" if sf_path else blk['name']
            if sub_path in z.namelist():
                sub_xml = z.read(sub_path).decode('utf-8')
                sub_root = ET.fromstring(sub_xml)
                blk['subsystem'] = parse_system(sub_root, z, sub_ref,
                                                sf_machine=sf_machine, sf_path=child_sf_path)

        blocks[sid] = blk

    # ---- raw connections (keep strings) -------------------------------
    connections = []
    for line in system_elem.findall('./Line'):
        src = line.find("./P[@Name='Src']").text if line.find("./P[@Name='Src']") is not None else None
        dsts = []
        dst = line.find("./P[@Name='Dst']").text if line.find("./P[@Name='Dst']") is not None else None
        if dst:
            dsts.append(dst)
        for br in line.findall('.//Branch'):
            p = br.find("./P[@Name='Dst']")
            bdst = p.text if (p is not None and p.text is not None) else None
            if bdst:
                dsts.append(bdst)
        connections.append({'src': src, 'dsts': dsts})

    # ---- attach port maps (only for subsystems) -----------------------
    # Root level does **not** have Inport/Outport blocks, but subsystems do.
    # We add the maps **after** the subsystem has been parsed.
    for blk in blocks.values():
        if 'subsystem' in blk:
            sub_model = blk['subsystem']
            in_map, out_map = _build_port_map(sub_model['blocks'])
            blk['input_ports'] = in_map          # e.g. {1: "error", 2: "ref"}
            blk['output_ports'] = out_map

    return {'blocks': blocks, 'connections': connections}


def parse_slx(slx_path: str) -> Dict:
    with zipfile.ZipFile(slx_path, 'r') as z:
        bd_xml = z.read('simulink/blockdiagram.xml').decode('utf-8')
        bd_root = ET.fromstring(bd_xml)

        sys_elem = bd_root.find('.//System')
        sys_ref = sys_elem.get('Ref') if sys_elem is not None else 'system_root'

        sys_path = f'simulink/systems/{sys_ref}.xml'
        sys_xml = z.read(sys_path).decode('utf-8')
        sys_root = ET.fromstring(sys_xml)

        sf_machine = load_stateflow_machine(z)
        return parse_system(sys_root, z, sys_ref, sf_machine=sf_machine)


# ----------------------------------------------------------------------
# 2. Recursive connection enrichment
# ----------------------------------------------------------------------
def _format_port(blk: Dict, port: int, is_output: bool) -> str:
    """Format a port reference as a human-readable string."""
    key = 'output_ports' if is_output else 'input_ports'
    prefix = 'Out' if is_output else 'In'
    if port == 0:
        return 'Enable'
    if key in blk:
        name = blk[key].get(port)
        base = f"{prefix}{port}"
        return f"{base}:{name}" if name else base
    return f"{prefix}{port}"


def _split_port(p: str):
    """'5#out:1' → (5, 'out', 1)"""
    sid_str, rest = p.split('#', 1)
    if  ':' not in rest:
       # case with enabled ports without port number (e.g., '5#enable')
       direction = rest
       port_str = '0'
    #    direction, port_str = rest.split(':', 1)
    else:
        direction, port_str = rest.split(':', 1)
    return sid_str, direction, int(port_str)


def enrich_connections(model: Dict) -> Dict:
    """Recursively enrich *all* levels (root + every subsystem)."""
    _enrich_one_level3(model)

    # recurse into subsystems
    for blk in model['blocks'].values():
        if 'subsystem' in blk:
            blk['subsystem'] = enrich_connections(blk['subsystem'])

    return model

def _enrich_one_level3(model: Dict):
    """Resolve Goto/From → direct RealSource → RealDest, keep both views."""
    blocks = model['blocks']

    # ------------------------------------------------------------------
    # 1. Build tag maps
    # ------------------------------------------------------------------
    goto_by_tag: Dict[str, List[int]] = defaultdict(list)
    from_by_tag: Dict[str, List[int]] = defaultdict(list)

    for sid, blk in blocks.items():
        tag = blk.get('parameters', {}).get('GotoTag')
        if not tag:
            continue
        if blk['type'] == 'Goto':
            goto_by_tag[tag].append(sid)
        elif blk['type'] == 'From':
            from_by_tag[tag].append(sid)

    # ------------------------------------------------------------------
    # 2. Find real sources feeding Goto blocks
    # ------------------------------------------------------------------
    real_sources: Dict[int, List[tuple[int, int]]] = defaultdict(list)  # goto_sid → [(src_sid, src_port)]
    goto_incoming_links = set()  # (src_sid, src_port, goto_sid, 1)

    for conn in model['connections']:
        src_str = conn['src']
        if not src_str:
            continue
        src_sid, _, src_port = _split_port(src_str)
        for dst_str in conn['dsts']:
            dst_sid, _, dst_port = _split_port(dst_str)
            if blocks[dst_sid]['type'] == 'Goto':
                real_sources[dst_sid].append((src_sid, src_port))
                goto_incoming_links.add((src_sid, src_port, dst_sid, dst_port))

    # ------------------------------------------------------------------
    # 3. Find real destinations fed by From blocks
    # ------------------------------------------------------------------
    real_destinations: Dict[int, List[tuple[int, int]]] = defaultdict(list)  # from_sid → [(dst_sid, dst_port)]
    from_outgoing_links = set()  # (from_sid, 1, dst_sid, dst_port)

    for conn in model['connections']:
        src_str = conn['src']
        if not src_str:
            continue
        src_sid, _, src_port = _split_port(src_str)
        if blocks[src_sid]['type'] != 'From':
            continue
        for dst_str in conn['dsts']:
            dst_sid, _, dst_port = _split_port(dst_str)
            real_destinations[src_sid].append((dst_sid, dst_port))
            from_outgoing_links.add((src_sid, src_port, dst_sid, dst_port))

    # ------------------------------------------------------------------
    # 4. Build real physical links (exclude Goto/From wires)
    # ------------------------------------------------------------------
    real_links = []
    virtual_resolved_links = []

    for conn in model['connections']:
        src_str = conn['src']
        if not src_str:
            continue
        src_sid, _, src_port = _split_port(src_str)
        src_blk = blocks[src_sid]

        for dst_str in conn['dsts']:
            dst_sid, _, dst_port = _split_port(dst_str)
            dst_blk = blocks[dst_sid]

            link = [src_sid, src_port, dst_sid, dst_port]

            # Skip any link involving Goto or From as source/destination
            if src_blk['type'] in ('Goto', 'From') or dst_blk['type'] in ('Goto', 'From'):
                continue

            real_links.append(link)

            # Normal population
            blocks[src_sid]['connection']['outgoing'].append({
                'dst_sid': dst_sid, 'src_port': src_port, 'dst_port': dst_port
            })
            blocks[dst_sid]['connection']['incoming'].append({
                'src_sid': src_sid, 'src_port': src_port, 'dst_port': dst_port
            })
            blocks[src_sid]['outputs'][src_port][dst_sid].append(dst_port)
            blocks[dst_sid]['inputs'][dst_port][src_sid].append(src_port)

    # ------------------------------------------------------------------
    # 5. Create direct virtual links: RealSource → RealDest
    # ------------------------------------------------------------------
    for tag, goto_sids in goto_by_tag.items():
        from_sids = from_by_tag.get(tag, [])
        if not from_sids:
            continue

        for goto_sid in goto_sids:
            sources = real_sources.get(goto_sid, [])
            for from_sid in from_sids:
                destinations = real_destinations.get(from_sid, [])
                for (src_sid, src_port) in sources:
                    for (dst_sid, dst_port) in destinations:
                        vlink = [src_sid, src_port, dst_sid, dst_port]
                        virtual_resolved_links.append(vlink)

                        # Also populate data structures so downstream code sees direct connection
                        blocks[src_sid]['connection']['outgoing'].append({
                            'dst_sid': dst_sid, 'src_port': src_port, 'dst_port': dst_port
                        })
                        blocks[dst_sid]['connection']['incoming'].append({
                            'src_sid': src_sid, 'src_port': src_port, 'dst_port': dst_port
                        })
                        blocks[src_sid]['outputs'][src_port][dst_sid].append(dst_port)
                        blocks[dst_sid]['inputs'][dst_port][src_sid].append(src_port)

    # ------------------------------------------------------------------
    # 6. Final outputs
    # ------------------------------------------------------------------
    # Only real wires (no Goto/From at all)
    model['connection_links'] = real_links

    # Real + resolved virtual = logical direct signal flow
    model['resolved_links'] = real_links + virtual_resolved_links

    # Human-readable: use resolved_links (clean logical view)
    readable = []
    for src_sid, src_port, dst_sid, dst_port in model['resolved_links']:
        src_blk = blocks[src_sid]
        dst_blk = blocks[dst_sid]
        src_port_name = _format_port(src_blk, src_port, True)
        dst_port_name = _format_port(dst_blk, dst_port, False)
        src_name = src_blk['name'].replace('\n', ' ')
        dst_name = dst_blk['name'].replace('\n', ' ')
        readable.append(f"{src_name}.{src_port_name} → {dst_name}.{dst_port_name}")

    model['connection_info'] = readable


def _stitch_links(links: List, skip_sids: set) -> List:
    """
    Contract pass-through nodes: for A→Skip→B, produce A→B directly.
    Matches by port number on the skip block (works for 1-in/1-out blocks).
    """
    skip_in: Dict[str, list] = defaultdict(list)   # skip_sid → [(src_sid, src_port, in_port)]
    skip_out: Dict[str, list] = defaultdict(list)  # skip_sid → [(dst_sid, dst_port, out_port)]

    for src_sid, src_port, dst_sid, dst_port in links:
        if dst_sid in skip_sids:
            skip_in[dst_sid].append((src_sid, src_port, dst_port))
        if src_sid in skip_sids:
            skip_out[src_sid].append((dst_sid, dst_port, src_port))

    stitched = []
    for sid in skip_sids:
        for (src_sid, src_port, in_port) in skip_in[sid]:
            for (dst_sid, dst_port, out_port) in skip_out[sid]:
                if in_port == out_port:  # same port number = same signal channel
                    stitched.append([src_sid, src_port, dst_sid, dst_port])

    kept = [l for l in links if l[0] not in skip_sids and l[2] not in skip_sids]
    return kept + stitched


def _links_to_connection_info(links: List, blocks: Dict) -> List[str]:
    """Regenerate human-readable connection strings from a link list."""
    result = []
    for src_sid, src_port, dst_sid, dst_port in links:
        src_blk = blocks.get(src_sid)
        dst_blk = blocks.get(dst_sid)
        if src_blk is None or dst_blk is None:
            continue
        src_str = _format_port(src_blk, src_port, True)
        dst_str = _format_port(dst_blk, dst_port, False)
        src_name = src_blk['name'].replace('\n', ' ')
        dst_name = dst_blk['name'].replace('\n', ' ')
        result.append(f"{src_name}.{src_str} → {dst_name}.{dst_str}")
    return result


# ----------------------------------------------------------------------
# 3. Filtering
# ----------------------------------------------------------------------
def filter_model_data(model: Dict, filters: Dict) -> Dict:
    skip = set(filters.get('skip_blocks', []))

    def _filter(blk: Dict) -> Dict:
        if blk.get('type') in skip:
            return None

        if blk.get('parameters', {}).get('Commented') == 'on':
            return {'name': blk.get('name'), 'type': blk.get('type'),
                    'sid': blk.get('sid'), 'disabled': True}

        f = {}
        for a in filters.get('default_attrs', []):
            if a in blk:
                f[a] = blk[a]

        pk = filters.get('default_params', []).copy()
        tm = filters.get('block_types', {})
        if blk.get('name') in tm:
            pk.extend(tm[blk['name']])
        elif blk.get('type') in tm:
            pk.extend(tm[blk['type']])
        # For Reference blocks, also apply rules keyed by SourceType (e.g. "Enumerated Constant")
        if blk.get('type') == 'Reference':
            source_type = blk.get('parameters', {}).get('SourceType', '')
            if source_type in tm:
                pk.extend(tm[source_type])

        if 'parameters' in blk and blk['parameters']:
            f['parameters'] = {k: v for k, v in blk['parameters'].items() if k in pk}
        if 'instance_data' in blk and blk['instance_data']:
            f['instance_data'] = {k: v for k, v in blk['instance_data'].items() if k in pk}

        # inject Simulink defaults for operator blocks that omit the param when default
        _OP_DEFAULTS = {'Logic': 'AND', 'RelationalOperator': '=='}
        btype = blk.get('type', '')
        if btype in _OP_DEFAULTS and 'Operator' in pk:
            f.setdefault('parameters', {}).setdefault('Operator', _OP_DEFAULTS[btype])

        # delete if empty
        if 'parameters' in f and not f['parameters']:
            del f['parameters']
        if 'instance_data' in f and not f['instance_data']:
            del f['instance_data']

        if 'stateflow' in blk:
            f['stateflow'] = blk['stateflow']

        if 'subsystem' in blk:
            f['subsystem'] = filter_model_data(blk['subsystem'], filters)

        return f

    all_blocks = model['blocks']
    filtered_blocks = {sid: r for sid, b in all_blocks.items() if (r := _filter(b)) is not None}
    kept_sids = set(filtered_blocks)

    # SIDs of blocks that exist but are being removed (pass-through candidates)
    skip_sids = {sid for sid, b in all_blocks.items() if b.get('type') in skip}

    # Stitch A→Skip→B into A→B, then drop any remaining skip references
    conn_links = _stitch_links(model.get('connection_links', []), skip_sids)
    virt_links = _stitch_links(model.get('virtual_links', []), skip_sids)
    resolved   = _stitch_links(model.get('resolved_links', []), skip_sids)

    conn_links = [l for l in conn_links if l[0] in kept_sids and l[2] in kept_sids]
    virt_links = [l for l in virt_links if l[0] in kept_sids and l[2] in kept_sids]

    conn_info = _links_to_connection_info(resolved, all_blocks) if skip_sids else model.get('connection_info', [])

    resolved_links = [l for l in resolved if l[0] in kept_sids and l[2] in kept_sids]
    return {
        'blocks': filtered_blocks,
        'connection_info': conn_info,
        'connection_links': conn_links,
        'resolved_links':   resolved_links,
    }


# ----------------------------------------------------------------------
# 4. High-level utilities
# ----------------------------------------------------------------------
def _clean(name: str) -> str:
    """Replace newlines in block names with spaces."""
    return name.replace('\n', ' ') if name else name


def _fmt_ports(port_map: Dict) -> str:
    return '  '.join(f"{k}:{v}" for k, v in sorted(port_map.items(), key=lambda x: int(x[0])))


# ----------------------------------------------------------------------
# Mermaid diagram helpers
# ----------------------------------------------------------------------
def _mid(sid: str) -> str:
    """Mermaid-safe node ID from block SID."""
    return f"N{re.sub(r'[^a-zA-Z0-9]', '_', str(sid))}"


def _mlabel(*parts: str) -> str:
    """Escape and join parts with <br/> for a Mermaid multi-line label."""
    cleaned = [str(p).replace('"', "'").replace('\n', ' ').strip() for p in parts if str(p).strip()]
    return '<br/>'.join(cleaned)


def _level_to_mermaid(model: Dict) -> str:
    """Generate a Mermaid flowchart LR for one level of a slim model."""
    blocks = model.get('blocks', {})
    links  = model.get('resolved_links', model.get('connection_links', []))
    lines  = ['flowchart LR']

    # --- nodes ---
    for sid, blk in blocks.items():
        nid   = _mid(sid)
        name  = _clean(blk.get('name', '?'))
        btype = blk.get('type', '')

        if btype in ('Inport', 'Outport'):
            lines.append(f'  {nid}(["{_mlabel(name)}"])')

        elif 'subsystem' in blk:
            in_p  = ', '.join(str(v) for _, v in sorted(blk.get('input_ports',  {}).items(), key=lambda x: int(x[0])))
            out_p = ', '.join(str(v) for _, v in sorted(blk.get('output_ports', {}).items(), key=lambda x: int(x[0])))
            parts = [name]
            if in_p:  parts.append(f'in: {in_p}')
            if out_p: parts.append(f'out: {out_p}')
            lines.append(f'  {nid}["{_mlabel(*parts)}"]')

        else:
            params = {**blk.get('parameters', {}), **blk.get('instance_data', {})}
            pstr   = '  '.join(f'{k}={v}' for k, v in params.items())
            parts  = [name, btype] + ([pstr] if pstr else [])
            lines.append(f'  {nid}["{_mlabel(*parts)}"]')

    # --- edges: group multiple outputs A→B into one labelled arrow ---
    edges: Dict[tuple, List[str]] = defaultdict(list)
    for src_sid, src_port, dst_sid, _ in links:
        if src_sid not in blocks or dst_sid not in blocks:
            continue
        label = _format_port(blocks[src_sid], src_port, is_output=True).replace('"', "'")
        edges[(src_sid, dst_sid)].append(label)

    for (src_sid, dst_sid), labels in edges.items():
        lines.append(f'  {_mid(src_sid)} -->|"{", ".join(labels)}"| {_mid(dst_sid)}')

    return '\n'.join(lines)


def model_to_markdown(model: Dict, title: str = 'Model', max_depth: int = 2) -> str:
    """
    Generate a Markdown report with hierarchical Mermaid architecture diagrams.

    max_depth controls how many subsystem levels are expanded:
      1 = root only
      2 = root + immediate subsystems (default)
      3 = one level deeper, etc.
    """
    sections: List[str] = [f'# {title} — Architecture\n']

    def _render(m: Dict, path: str, depth: int) -> None:
        h = '#' * min(depth + 1, 6)
        sections.append(f'\n{h} {path}\n')
        sections.append('```mermaid')
        sections.append(_level_to_mermaid(m))
        sections.append('```\n')
        if depth < max_depth:
            for blk in m.get('blocks', {}).values():
                if 'subsystem' in blk:
                    _render(blk['subsystem'], f'{path} > {_clean(blk["name"])}', depth + 1)

    _render(model, title, 1)
    return '\n'.join(sections)


def model_to_text(model: Dict, path: str = 'root') -> str:
    """
    Produce a flat, LLM-friendly text report of a slim model.
    Subsystems are expanded inline after the parent level, with breadcrumb headers.
    """
    blocks = model.get('blocks', {})
    lines = []

    # ---- classify blocks -----------------------------------------------
    disabled   = [(s, b) for s, b in blocks.items() if b.get('disabled')]
    disabled_sids = {s for s, _ in disabled}
    inports    = [(s, b) for s, b in blocks.items() if b.get('type') == 'Inport']
    outports   = [(s, b) for s, b in blocks.items() if b.get('type') == 'Outport']
    gotos      = [(s, b) for s, b in blocks.items() if b.get('type') == 'Goto']
    froms      = [(s, b) for s, b in blocks.items() if b.get('type') == 'From']
    charts     = [(s, b) for s, b in blocks.items()
                  if 'stateflow' in b and s not in disabled_sids]
    chart_sids = {s for s, _ in charts}
    subsystems = [(s, b) for s, b in blocks.items()
                  if 'subsystem' in b and s not in chart_sids and s not in disabled_sids]
    other      = [(s, b) for s, b in blocks.items()
                  if b.get('type') not in ('Inport', 'Outport', 'Goto', 'From')
                  and 'subsystem' not in b and s not in chart_sids and s not in disabled_sids]

    # ---- header --------------------------------------------------------
    lines.append(f"\n=== {path} ===")

    if inports:
        names = ' | '.join(_clean(b['name']) for _, b in inports)
        lines.append(f"INPUTS:  {names}")
    if outports:
        names = ' | '.join(_clean(b['name']) for _, b in outports)
        lines.append(f"OUTPUTS: {names}")

    # ---- stateflow charts (one-liner each, with port info) -------------
    if charts:
        lines.append("CHARTS:")
        for _, b in charts:
            in_p  = _fmt_ports(b.get('input_ports', {}))
            out_p = _fmt_ports(b.get('output_ports', {}))
            ports = ''
            if in_p:  ports += f"  in[{in_p}]"
            if out_p: ports += f"  out[{out_p}]"
            lines.append(f"  {_clean(b['name'])}  [Chart]{ports}")

    # ---- subsystems (one-liner each) -----------------------------------
    if subsystems:
        lines.append("SUBSYSTEMS:")
        for _, b in subsystems:
            in_p  = _fmt_ports(b.get('input_ports', {}))
            out_p = _fmt_ports(b.get('output_ports', {}))
            ports = ''
            if in_p:  ports += f"  in[{in_p}]"
            if out_p: ports += f"  out[{out_p}]"
            lines.append(f"  {_clean(b['name'])}{ports}")

    # ---- disabled (commented-out) blocks --------------------------------
    if disabled:
        lines.append("DISABLED (commented-out, not in code gen):")
        for _, b in disabled:
            btype = b.get('type', '?')
            lines.append(f"  {_clean(b['name'])}  [{btype}]")

    # ---- computation blocks --------------------------------------------
    if other:
        lines.append("BLOCKS:")
        for _, b in other:
            btype = b.get('type', '?')
            if btype == 'Reference':
                btype = b.get('parameters', {}).get('SourceType') or btype
            bname = _clean(b.get('name', '?'))
            params = {**b.get('parameters', {}), **b.get('instance_data', {})}
            # SourceType is already shown as the type tag; SourceBlock is internal path noise
            params.pop('SourceType', None)
            params.pop('SourceBlock', None)
            pstr = ('  ' + '  '.join(f"{k}={_clean(str(v))}" for k, v in params.items())) if params else ''
            lines.append(f"  [{btype}]  {bname}{pstr}")

    # ---- virtual signal routing ----------------------------------------
    if gotos or froms:
        lines.append("VIRTUAL SIGNALS (Goto→From):")
        tags = dict.fromkeys(  # preserve insertion order
            b.get('parameters', {}).get('GotoTag', '') for _, b in gotos)
        for tag in tags:
            g_names = [_clean(b['name']) for _, b in gotos
                       if b.get('parameters', {}).get('GotoTag') == tag]
            f_names = [_clean(b['name']) for _, b in froms
                       if b.get('parameters', {}).get('GotoTag') == tag]
            rhs = ', '.join(f_names) if f_names else '(none)'
            lines.append(f"  {tag:<20}  {', '.join(g_names)} → {rhs}")

    # ---- signal flow ---------------------------------------------------
    conn = model.get('connection_info', [])
    if conn:
        lines.append("SIGNAL FLOW:")
        for c in conn:
            lines.append(f"  {_clean(c)}")

    # ---- expand stateflow charts inline --------------------------------
    sf_texts = []
    for _, b in charts:
        chart_path = f"{path} > {_clean(b['name'])}"
        sf_texts.append(_sf_render(b['stateflow'], chart_path))

    # ---- recurse into subsystems ---------------------------------------
    sub_texts = []
    for _, b in subsystems:
        child_path = f"{path} > {_clean(b['name'])}"
        sub_texts.append(model_to_text(b['subsystem'], path=child_path))

    return '\n'.join(lines) + ''.join(sf_texts) + ''.join(sub_texts)


ALL_OUTPUTS = ['full.json', 'slim.json', 'slim.min.json', 'report.txt', 'arch.md', 'sf.yaml', 'sf.m']


def slx_process(slx_path: str, filters: Dict, save: bool = True,
                output_dir: str = None, outputs: list = None) -> Dict:
    """Parse, enrich, filter one SLX file. Optionally save outputs.

    output_dir : write files here instead of next to the .slx (optional)
    outputs    : list of file suffixes to produce, e.g. ['report.txt', 'arch.md']
                 defaults to ALL_OUTPUTS when None
    """
    name = Path(slx_path).stem
    full = parse_slx(slx_path)
    full = enrich_connections(full)
    slim = filter_model_data(full, filters)

    if save:
        base = Path(output_dir) / name if output_dir else Path(slx_path).with_suffix('')
        emit = set(outputs) if outputs is not None else set(ALL_OUTPUTS)
        if 'full.json' in emit:
            with open(f'{base}_full.json', 'w', encoding='utf-8') as f:
                json.dump(full, f, indent=2, ensure_ascii=False)
        if 'slim.json' in emit:
            with open(f'{base}_slim.json', 'w', encoding='utf-8') as f:
                json.dump(slim, f, indent=2, ensure_ascii=False)
        if 'slim.min.json' in emit:
            with open(f'{base}_slim.min.json', 'w', encoding='utf-8') as f:
                json.dump(slim, f, separators=(',', ':'), ensure_ascii=False)
        if 'report.txt' in emit:
            with open(f'{base}_report.txt', 'w', encoding='utf-8') as f:
                f.write(model_to_text(slim, path=name))
        if 'arch.md' in emit:
            with open(f'{base}_arch.md', 'w', encoding='utf-8') as f:
                f.write(model_to_markdown(slim, title=name))
        if 'sf.yaml' in emit or 'sf.m' in emit:
            charts = _collect_sf_charts(slim)
            for chart_name, sf in charts.items():
                # prefer the chart's own full-path name (e.g. CONTROL/Chart1) for uniqueness
                file_stem = sf.get('name') or chart_name
                safe = re.sub(r'[^\w\-]', '_', file_stem)
                chart_dict = stateflow_chart_to_dict(sf)
                if 'sf.yaml' in emit:
                    path_out = Path(base).parent / f'{safe}_sf.yaml'
                    with open(path_out, 'w', encoding='utf-8') as f:
                        yaml.dump(chart_dict, f, Dumper=_SFYamlDumper,
                                  allow_unicode=True, sort_keys=False, default_flow_style=False)
                    print(f"  wrote sf.yaml: {path_out.name}")
                if 'sf.m' in emit:
                    path_out = Path(base).parent / f'{safe}_sf.m'
                    path_out.write_text(stateflow_dict_to_matlab(chart_dict), encoding='utf-8')
                    print(f"  wrote sf.m:    {path_out.name}")

    return slim


def collect_refs(slim: Dict) -> set:
    """Recursively collect all ReferencedSubsystem names from a slim dict."""
    refs = set()
    for blk in slim.get('blocks', {}).values():
        ref = blk.get('parameters', {}).get('ReferencedSubsystem')
        if ref:
            refs.add(ref)
        if 'subsystem' in blk:
            refs |= collect_refs(blk['subsystem'])
    return refs


def collect_lib_refs(slim: Dict) -> set:
    """Recursively collect library names from SourceBlock params (e.g. 'TmsCtlr_lib' from 'TmsCtlr_lib/Block')."""
    libs = set()
    for blk in slim.get('blocks', {}).values():
        src = blk.get('parameters', {}).get('SourceBlock', '')
        if '/' in src:
            libs.add(src.split('/')[0])
        if 'subsystem' in blk:
            libs |= collect_lib_refs(blk['subsystem'])
    return libs


def find_slx(name: str, root: str) -> str:
    """Search root folder recursively for <name>.slx. Returns first match path or None."""
    for p in Path(root).rglob(f'{name}.slx'):
        return str(p)
    return None


def process_model_tree(slx_path: str, filters: Dict, proj_root: str,
                       save: bool = True, output_dir: str = None,
                       outputs: list = None,
                       parse_libraries: bool = False) -> Dict[str, Dict]:
    """Process a model and all referenced sub-models recursively.

    Searches proj_root for any ReferencedSubsystem .slx files found in each
    processed model. Returns dict of {model_name: slim} for all processed models.
    save            : write output files (default True); set False to parse only
    output_dir      : write files here instead of next to each .slx (optional)
    outputs         : list of file suffixes to produce; defaults to ALL_OUTPUTS when None
    parse_libraries : also parse referenced Simulink libraries (SourceBlock refs)
                      and save their reports into a 'libraries/' sub-folder
    """
    queue = [slx_path]
    processed = {}  # name -> slim

    while queue:
        path = queue.pop(0)
        name = Path(path).stem
        if name in processed:
            continue
        print(f"\n{'='*60}\nProcessing: {name}\n  {path}")
        slim = slx_process(path, filters, save=save, output_dir=output_dir, outputs=outputs)
        processed[name] = slim
        print(f"  blocks: {len(slim['blocks'])}  connections: {len(slim.get('connection_info', []))}")

        for ref in sorted(collect_refs(slim)):
            if ref not in processed:
                ref_path = find_slx(ref, proj_root)
                if ref_path:
                    print(f"  -> queued ref: {ref}")
                    queue.append(ref_path)
                else:
                    print(f"  [WARN] referenced model not found: {ref}")

    if parse_libraries and save:
        all_libs = set()
        for slim in processed.values():
            all_libs |= collect_lib_refs(slim)

        lib_dir = (Path(output_dir) / 'libraries') if output_dir else None
        if lib_dir:
            lib_dir.mkdir(exist_ok=True)

        lib_processed = {}
        for lib_name in sorted(all_libs):
            if lib_name in lib_processed:
                continue
            lib_path = find_slx(lib_name, proj_root)
            if lib_path:
                print(f"\n{'='*60}\nProcessing library: {lib_name}\n  {lib_path}")
                lib_slim = slx_process(lib_path, filters, save=save,
                                       output_dir=str(lib_dir) if lib_dir else None,
                                       outputs=outputs)
                lib_processed[lib_name] = lib_slim
                print(f"  blocks: {len(lib_slim['blocks'])}")
            else:
                print(f"  [WARN] library not found: {lib_name}")

    if save and output_dir:
        _write_llm_readme(output_dir, processed, outputs or ALL_OUTPUTS)

    return processed


def _write_llm_readme(output_dir: str, processed: Dict[str, Dict], outputs: list) -> None:
    """Write README.md into output_dir explaining how to read the reports."""
    emit = set(outputs)
    root_name = next(iter(processed))  # first processed = entry-point model

    # Build model reference tree: name -> set of direct refs
    ref_tree = {name: sorted(collect_refs(slim)) for name, slim in processed.items()}

    lines = []
    lines += [
        '# LLM Guide — Simulink Model Reports',
        '',
        'This folder contains exported reports for a Simulink model tree.',
        f'Entry-point model: **{root_name}**',
        '',
    ]

    # File types legend
    lines += ['## File Types', '']
    if 'report.txt' in emit:
        lines.append('- `_report.txt` — **Primary analysis file.** Flat text listing every block with '
                     'type and key parameters, followed by full signal flow. '
                     'Subsystems are expanded inline with breadcrumb headers. '
                     'Use this to understand control logic and signal routing.')
    if 'arch.md' in emit:
        lines.append('- `_arch.md` — **Architecture overview.** Mermaid flowcharts per subsystem level. '
                     'Use this to understand structure and data flow between subsystems before diving into detail.')
    if 'slim.min.json' in emit:
        lines.append('- `_slim.min.json` — Filtered model as minified JSON. Useful for programmatic access.')
    if 'slim.json' in emit:
        lines.append('- `_slim.json` — Filtered model as formatted JSON.')
    if 'full.json' in emit:
        lines.append('- `_full.json` — Complete unfiltered model. Large; for debugging only.')
    lines.append('')

    # Model tree
    lines += ['## Model Tree', '']
    lines.append(f'- **{root_name}** ← entry point')
    for name in ref_tree:
        if name == root_name:
            continue
        referrers = [p for p, r in ref_tree.items() if name in r]
        lines.append(f'- **{name}** ← referenced by: {", ".join(referrers)}')
    lines.append('')

    # Reading strategy
    lines += [
        '## Suggested Reading Order',
        '',
        f'1. `{root_name}_arch.md` — get the top-level architecture: which subsystems exist and how they connect.',
        f'2. `{root_name}_report.txt` — understand root-level signal routing and which signals feed into each subsystem.',
        '3. For each subsystem of interest, open its own `_report.txt` for detailed block-level analysis.',
        '4. Use `_arch.md` files for any subsystem that has multiple nested levels.',
        '',
    ]

    # Notation reference
    lines += [
        '## Notation in `_report.txt`',
        '',
        '**Signal flow lines:**',
        '```',
        'Source.OutN:signal_name → Dest.InM:port_name',
        '```',
        'Reads as: output port N of block `Source` (named `signal_name`) connects to input port M of block `Dest`.',
        '',
        '**Subsystem expansion — breadcrumb headers:**',
        '```',
        '=== root > SubsystemA > SubsystemB ===',
        '```',
        'Everything below this header belongs to that subsystem scope.',
        '',
        '**Virtual signals (Goto/From):**',
        '```',
        'VIRTUAL SIGNALS (Goto→From):',
        '  SignalName   GotoBlock → FromBlock1, FromBlock2',
        '```',
        'Simulink Goto/From blocks are named signal buses. They are resolved as direct connections '
        'in the SIGNAL FLOW section — no need to trace them manually.',
        '',
        '**Cross-reference with generated C code:**',
        'Simulink Coder embeds SID references in C comments:',
        '```c',
        'real32_T foo;  /* \'<S22>/Constant14\' */',
        '```',
        '`S22` is a subsystem SID, `Constant14` is the block name. '
        'Block SIDs are listed in `_report.txt` next to each block name.',
        '',
    ]

    # Files in this folder
    lines += ['## Files in This Folder', '']
    for name in processed:
        for suffix in ['report.txt', 'arch.md', 'slim.min.json', 'slim.json', 'full.json']:
            if suffix in emit:
                lines.append(f'- `{name}_{suffix}`')
    lines.append('')

    out_path = Path(output_dir) / 'README.md'
    out_path.write_text('\n'.join(lines), encoding='utf-8')
    print(f"  wrote: {out_path}")


def compare_models(models: Dict[str, Dict]) -> Dict:
    """Compare block-name sets across models. Returns diff dict."""
    names = list(models)
    if len(names) < 2:
        return {}

    a_name, b_name = names[0], names[1]
    a_blocks = {b['name'].replace('\n', ' ') for b in models[a_name]['blocks'].values() if 'name' in b}
    b_blocks = {b['name'].replace('\n', ' ') for b in models[b_name]['blocks'].values() if 'name' in b}

    diff = {
        'common':       sorted(a_blocks & b_blocks),
        f'only_{a_name}': sorted(a_blocks - b_blocks),
        f'only_{b_name}': sorted(b_blocks - a_blocks),
    }

    print(f"\n{'='*60}")
    print(f"Comparison: {a_name}  vs  {b_name}")
    print(f"  Common blocks       : {len(diff['common'])}")
    print(f"  Only in {a_name}: {len(diff[f'only_{a_name}'])}")
    for n in diff[f'only_{a_name}']:
        print(f"    - {n}")
    print(f"  Only in {b_name}: {len(diff[f'only_{b_name}'])}")
    for n in diff[f'only_{b_name}']:
        print(f"    + {n}")

    return diff


def compare_model_trees(results_a: Dict[str, Dict], name_a: str,
                        results_b: Dict[str, Dict], name_b: str,
                        output_path: str = None) -> str:
    """Compare two model trees (each a dict of {model_name: slim}).

    Produces a Markdown report showing:
    - which models are new / removed / shared between the two trees
    - for each shared model: added blocks, removed blocks, changed block params

    If output_path is given, writes the report there and prints the path.
    Returns the report as a string.
    """
    def _block_map(slim):
        """Flat dict of block_name -> {type, parameters, instance_data} from slim."""
        result = {}
        for blk in slim.get('blocks', {}).values():
            n = blk.get('name', '').replace('\n', ' ')
            if n:
                result[n] = blk
        return result

    def _params(blk):
        p = {}
        p.update(blk.get('parameters', {}))
        p.update(blk.get('instance_data', {}))
        return p

    lines = [
        f'# Model Tree Comparison: {name_a} vs {name_b}',
        '',
        f'**A:** {name_a}  ({len(results_a)} models)',
        f'**B:** {name_b}  ({len(results_b)} models)',
        '',
    ]

    keys_a, keys_b = set(results_a), set(results_b)
    only_a = sorted(keys_a - keys_b)
    only_b = sorted(keys_b - keys_a)
    shared = sorted(keys_a & keys_b)

    lines += ['## Model-level Diff', '']
    if only_a:
        lines.append(f'**Only in {name_a}:** ' + ', '.join(f'`{m}`' for m in only_a))
    if only_b:
        lines.append(f'**Only in {name_b}:** ' + ', '.join(f'`{m}`' for m in only_b))
    lines.append(f'**Shared:** ' + ', '.join(f'`{m}`' for m in shared))
    lines.append('')

    lines += ['## Block-level Diff (shared models)', '']

    for model in shared:
        bmap_a = _block_map(results_a[model])
        bmap_b = _block_map(results_b[model])
        names_a, names_b = set(bmap_a), set(bmap_b)

        added   = sorted(names_b - names_a)
        removed = sorted(names_a - names_b)

        # param changes for blocks present in both
        changed = []
        for n in sorted(names_a & names_b):
            pa, pb = _params(bmap_a[n]), _params(bmap_b[n])
            all_keys = set(pa) | set(pb)
            diffs = {k: (pa.get(k), pb.get(k)) for k in all_keys if pa.get(k) != pb.get(k)}
            if diffs:
                changed.append((n, bmap_a[n].get('type', '?'), diffs))

        if not added and not removed and not changed:
            lines.append(f'### `{model}` — no changes')
            lines.append('')
            continue

        lines.append(f'### `{model}`')
        lines.append('')

        if added:
            lines.append(f'**Added blocks** ({len(added)}):')
            for n in added:
                lines.append(f'  - [{bmap_b[n].get("type","?")}] {n}')
            lines.append('')

        if removed:
            lines.append(f'**Removed blocks** ({len(removed)}):')
            for n in removed:
                lines.append(f'  - [{bmap_a[n].get("type","?")}] {n}')
            lines.append('')

        if changed:
            lines.append(f'**Changed parameters** ({len(changed)} blocks):')
            for n, btype, diffs in changed:
                lines.append(f'  - [{btype}] {n}')
                for k, (va, vb) in diffs.items():
                    lines.append(f'      {k}: `{va}` → `{vb}`')
            lines.append('')

    report = '\n'.join(lines)

    if output_path:
        Path(output_path).write_text(report, encoding='utf-8')
        print(f"Comparison written: {output_path}")

    return report


# ----------------------------------------------------------------------
# 5. Example usage
# ----------------------------------------------------------------------
if __name__ == '__main__':
    FILTERS = {
        'default_attrs': ['name', 'type','input_ports','output_ports'],
        'default_params': [],
        'block_types': {
            'Constant': ['Value'],
            'Sum': ['Inputs'],
            'TransferFcn': ['Denominator', 'Numerator'],
            'PID Controller': ['P', 'I', 'D', 'N', 'Form', 'TimeDomain'],
            'Discrete PID Controller': ['P', 'I', 'D', 'N', 'Form', 'TimeDomain'],
            'Switch': ['Criteria'],
            'From': ['GotoTag'],
            'Goto': ['GotoTag'],
            'EnablePort':['StatesWhenEnabling']
        },
        'skip_blocks': ['SignalConversion', 'DataTypeConversion'],
    }
    filein = r'data/model/pid_control_ex1.slx'
    filein = r'C:\Users\ivanm\Documents\MATLAB\EKL\dp187_ravo\dp187_csw\models\APP_FUN\subs\APP_SENSORS.slx'
    filein = r'C:\Users\ivanm\Documents\MATLAB\EKL\dp190_ox\dp190_ox_csw\.deps\comps\apps\tcuapp0\models\TmsCtlr_lib.slx'
    filefull_out = filein.replace('.slx','_full.json')
    filelim_out = filein.replace('.slx','_slim.json')
    fileslimmin_out = filein.replace('.slx','_slim.min.json')
    full = parse_slx(filein )

    full = enrich_connections(full)
    slim = filter_model_data(full, FILTERS)

    # Save both full and minified versions
    
    with open(filefull_out, 'w', encoding='utf-8') as f:
        json.dump(full, f, indent=2, ensure_ascii=False)
    with open(filelim_out, 'w', encoding='utf-8') as f:
        json.dump(slim, f, indent=2, ensure_ascii=False)
        
    with open(fileslimmin_out, 'w', encoding='utf-8') as f:
        json.dump(slim, f, separators=(',', ':'), ensure_ascii=False)

    print("\n--- Sample human-readable connections (root) ---")
    for line in slim.get('connection_info', [])[:8]:
        print(line)