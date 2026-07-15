import sys
import xml.etree.ElementTree as ET
import math
import re
import yaml
from pathlib import Path
from typing import Dict, List, Any

try:
    from slxgen.elk_layout import (sf_to_elk_json, elk_layout, elk_to_stateflow_layout,  # noqa: F401
                                    elk_layout_bottomup, _DEFAULT_TRANS_OFFSET)
    _ELK_AVAILABLE = True
except ImportError:
    _ELK_AVAILABLE = False
    _DEFAULT_TRANS_OFFSET = 20

from slxgen.stateflow_sir import yaml_to_sir, sir_validate, sir_to_chart_dict, format_description as _format_description


# ----------------------------------------------------------------------
# SLX parsing — Stateflow machine + chart XML
# ----------------------------------------------------------------------

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

    Handles both Stateflow's native /action format and the {action} API format:
      '[cond]/act'                → trigger='', condition='cond', action='act'
      '[cond]'                    → trigger='', condition='cond', action=''
      '[cond]{act}'               → trigger='', condition='cond', action='act'
      'after(N,tick)[cond]/act'   → trigger='after(N,tick)', condition='cond', action='act'
      'evtName'                   → trigger='evtName', condition='', action=''

    The YAML trigger field holds pure Stateflow events (after(), named events).
    Conditions in [brackets] map to the condition field.
    """
    trigger, condition, action = '', '', ''
    # Primary: handle [condition] followed by /action or {action}
    m = re.match(
        r'^(.*?)\s*\[([^\]]*)\]\s*(?:/(.*)|\{([^}]*)\})?$',
        label.strip(), re.DOTALL,
    )
    if m:
        trigger   = m.group(1).strip()
        condition = m.group(2).strip()
        action    = (m.group(3) or m.group(4) or '').strip()
    else:
        # No condition bracket — try trigger/action or trigger{action}
        m2 = re.match(r'^(.*?)\s*/(.+)$', label.strip(), re.DOTALL)
        m3 = re.match(r'^(.*?)\s*\{([^}]*)\}$', label.strip(), re.DOTALL)
        if m2:
            trigger = m2.group(1).strip()
            action  = m2.group(2).strip()
        elif m3:
            trigger = m3.group(1).strip()
            action  = m3.group(2).strip()
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
_SF_LEAF_W       = 150   # minimum width of a leaf state
_SF_PX_PER_CHAR  = 7.5   # approximate px width per character in Stateflow label font
_SF_LEAF_H       = 80    # minimum height of a leaf state
_SF_HEADER_H     = 30    # top strip reserved for state name label inside a parent
_SF_PADDING      = 20    # inner padding between parent edge and children
_SF_GAP          = 20    # gap between sibling states
_SF_MAX_COLS     = 4     # max columns in a single row (horizontal layout)
_SF_VERT_THRESH  = 5     # switch to single-column vertical layout above this many normal children
_SF_LABEL_LINE_H  = 16   # pixels per text line in label
_SF_LABEL_PAD     = 20   # top+bottom padding inside leaf label area
_SF_LABEL_STAGGER = 25   # min vertical separation between stacked transition labels (px)


def _bfs_order(states_dict: Dict, transitions: list,
               path_prefix: str, skip_names: set) -> list:
    """Return normal-state names ordered by BFS from the default state.

    Visits states in execution-flow order by following outgoing transitions.
    States unreachable from the start node are appended in original dict order.
    Cyclic transitions are handled safely (each node visited at most once).
    """
    from collections import deque
    child_names = [n for n in states_dict if n not in skip_names]
    if not child_names:
        return []

    child_set = set(child_names)

    # Build adjacency list: src → [dst, ...] (only within this container)
    adj: Dict[str, list] = {n: [] for n in child_names}
    for t in transitions:
        src = _direct_child_name(t.get('from', ''), path_prefix)
        dst = _direct_child_name(t.get('to', ''), path_prefix)
        if src in child_set and dst in child_set and src != dst and dst not in skip_names:
            if dst not in adj[src]:
                adj[src].append(dst)

    # Start from the explicit default state; fall back to first in dict order
    start = next((n for n in states_dict if states_dict[n].get('default') and n not in skip_names),
                 child_names[0])

    visited: set = set()
    result: list = []
    queue: deque = deque([start])
    while queue:
        node = queue.popleft()
        if node in visited:
            continue
        visited.add(node)
        result.append(node)
        for neighbor in adj[node]:
            if neighbor not in visited:
                queue.append(neighbor)

    # Append anything not reachable from start (e.g. disconnected states)
    for name in child_names:
        if name not in visited:
            result.append(name)

    return result


def _sf_label_height(state_body: Dict) -> int:
    """Minimum pixel height for a leaf state based on label line count."""
    lines = 1  # state name
    for kw in ('en', 'du', 'ex'):
        text = state_body.get(kw, '').strip()
        if text:
            lines += 1                       # "kw:" keyword line
            lines += len(text.split('\n'))   # code lines
    return max(_SF_LEAF_H, _SF_LABEL_LINE_H * lines + _SF_LABEL_PAD)


def _sf_leaf_width(state_body: Dict, name: str = '') -> int:
    """Estimate leaf state pixel width from the longest label line."""
    lines = [name] if name else []
    for kw in ('en', 'du', 'ex'):
        text = state_body.get(kw, '').strip()
        if text:
            lines.append(kw + ':')
            lines.extend(text.split('\n'))
    if not lines:
        return _SF_LEAF_W
    max_chars = max(len(l) for l in lines)
    return max(_SF_LEAF_W, int(max_chars * _SF_PX_PER_CHAR) + 20)


def _label_gap_bounds(src_path: str, dst_path: str, positions: dict,
                      margin: int = 5) -> tuple:
    """Return (y_min, y_max) bounding the gap between src and dst states.

    For a forward (downward) edge: gap = [src_bottom + margin, dst_top - margin].
    Returns (None, None) for back-edges, missing positions, or zero-height gaps.
    """
    if src_path not in positions or dst_path not in positions:
        return None, None
    _, src_y, _, src_h = positions[src_path]
    _, dst_y, _,  _    = positions[dst_path]
    lo = src_y + src_h + margin
    hi = dst_y - margin
    return (lo, hi) if lo < hi else (None, None)


def _stagger_label_y(mid_y: int, y_min, y_max, used: list, step: int) -> int:
    """Find a y near mid_y that does not collide with any entry in `used` (within ±step).

    Alternates above/below mid_y in `step` increments; clamps to [y_min, y_max].
    Falls back to mid_y if no free slot is found within 10 steps.
    """
    def _ok(y: int) -> bool:
        if y_min is not None and y < y_min:
            return False
        if y_max is not None and y > y_max:
            return False
        return not any(abs(y - u) < step for u in used)

    if _ok(mid_y):
        return mid_y
    for n in range(1, 11):
        for candidate in (mid_y + n * step, mid_y - n * step):
            if _ok(candidate):
                return candidate
    return mid_y


def _push_label_outside_states(mid_x: int, mid_y: int, positions: dict,
                                src_path: str = '', dst_path: str = '',
                                margin: int = 15) -> tuple:
    """Push label anchor (mid_x, mid_y) down when it lands inside an intermediate state.

    Excluded from the check: ancestors of src/dst (they contain the arc endpoints)
    and all descendants of src/dst (subchart-internal states whose chart-absolute
    coordinates may extend outside their parent's visible collapsed area).

    Additionally, only pushes when the resulting y stays within the arc's bounding
    y-extent (max of src/dst bottoms + margin), so that large sibling states in
    subchart-relative coordinate spaces cannot push labels far below the chart.

    Returns (mid_x, mid_y).
    """
    def _ancestors(path: str) -> set:
        if not path:
            return set()
        parts = path.split('.')
        return {'.'.join(parts[:i + 1]) for i in range(len(parts))}

    # Exclude ancestors AND descendants of both src and dst
    related = _ancestors(src_path) | _ancestors(dst_path)
    excluded = {p for p in positions
                if p in related
                or (src_path and (p == src_path or p.startswith(src_path + '.')))
                or (dst_path and (p == dst_path or p.startswith(dst_path + '.')))}

    # Upper bound: don't push past the lower of the two state bottoms + margin
    if src_path in positions and dst_path in positions:
        _, sy, _, sh = positions[src_path]
        _, dy, _, dh = positions[dst_path]
        y_ceiling = max(sy + sh, dy + dh) + margin
    else:
        y_ceiling = None

    for state_path, (px, py, pw, ph) in positions.items():
        pushed = py + ph + margin
        if (state_path not in excluded
                and px <= mid_x <= px + pw
                and py <= mid_y <= py + ph
                and (y_ceiling is None or pushed <= y_ceiling)):
            return mid_x, pushed
    return mid_x, mid_y


def _direct_child_name(path: str, prefix: str) -> str:
    """Return the first path component under prefix, or '' if path is not a descendant."""
    if prefix:
        if not path.startswith(prefix + '.'):
            return ''
        rest = path[len(prefix) + 1:]
    else:
        rest = path
    return rest.split('.')[0] if rest else ''


_SINK_KEYWORDS: tuple = ('FAULT', 'ERROR')


def _find_sink_states(states_dict: Dict, transitions: list, path_prefix: str) -> set:
    """Return names of direct children that qualify as ELK LAST-layer sinks.

    A sink has no outgoing transitions to siblings AND meets at least one of:
      - name contains a sink keyword ('FAULT', 'ERROR')
      - explicit role: sink/fault/error annotation in the YAML body

    Requires at least 2 non-sink siblings to avoid false-positive sidebars at root.
    """
    from slxgen.elk_layout import _SINK_ROLE_ALIASES  # noqa: PLC0415
    child_names = set(states_dict.keys())
    froms: set = set()
    for t in transitions:
        src_top = _direct_child_name(t.get('from', ''), path_prefix)
        dst_top = _direct_child_name(t.get('to', ''), path_prefix)
        if src_top in child_names and dst_top in child_names and src_top != dst_top:
            froms.add(src_top)
    pure_sinks = child_names - froms
    sink_children = {
        s for s in pure_sinks
        if any(kw in s.upper() for kw in _SINK_KEYWORDS)
        or states_dict[s].get('role', '').lower() in _SINK_ROLE_ALIASES
    }
    if len(child_names) - len(sink_children) < 2:
        return set()
    return sink_children


def _compute_auto_sinks(states_dict: Dict, transitions: list,
                         min_incoming: int) -> 'frozenset[str]':
    """Topological sink detection: return dotted paths of states that are pure sinks
    (no outgoing transitions to siblings) with at least min_incoming from siblings.

    Used when the __auto_sink__ elk_option is set. The result is passed to
    sf_to_elk_json() and elk_to_stateflow_layout() as auto_sinks.
    """
    all_paths: set = set()

    def _walk(d: dict, prefix: str) -> None:
        for name, body in d.items():
            path = f'{prefix}.{name}' if prefix else name
            all_paths.add(path)
            _walk(body.get('states', {}), path)

    _walk(states_dict, '')

    incoming: Dict[str, int] = {p: 0 for p in all_paths}
    outgoing: Dict[str, int] = {p: 0 for p in all_paths}

    for tr in transitions:
        src = tr.get('from', '')
        dst = tr.get('to', '')
        if not src or src not in all_paths or dst not in all_paths:
            continue
        src_parent = src.rsplit('.', 1)[0] if '.' in src else ''
        dst_parent = dst.rsplit('.', 1)[0] if '.' in dst else ''
        if src_parent == dst_parent:
            outgoing[src] = outgoing.get(src, 0) + 1
            incoming[dst] = incoming.get(dst, 0) + 1

    return frozenset(
        p for p in all_paths
        if outgoing.get(p, 0) == 0 and incoming.get(p, 0) >= min_incoming
    )


def _sf_state_size(state_body: Dict, transitions=None, path_prefix: str = '',
                   adaptive_leaf_width: bool = False, name: str = '') -> tuple:
    """Return (width, height) required to render this state and all its children."""
    if state_body.get('junction'):
        return (20, 20)
    children = state_body.get('states', {})
    if not children:
        w = _sf_leaf_width(state_body, name) if adaptive_leaf_width else _SF_LEAF_W
        return (w, _sf_label_height(state_body))

    names = list(children.keys())

    sink_names = _find_sink_states(children, transitions or [], path_prefix) if transitions is not None else set()
    if transitions is not None:
        normal_names = _bfs_order(children, transitions, path_prefix, sink_names)
    else:
        normal_names = [n for n in names if n not in sink_names]
    sink_list = [n for n in names if n in sink_names]
    if not normal_names:
        normal_names = names
        sink_list = []

    def child_prefix(cname):
        return f'{path_prefix}.{cname}' if path_prefix else cname

    sizes = {cname: _sf_state_size(children[cname], transitions, child_prefix(cname),
                                    adaptive_leaf_width=adaptive_leaf_width, name=cname)
             for cname in names}

    n = len(normal_names)
    cols = 1 if n > _SF_VERT_THRESH else min(n, _SF_MAX_COLS)
    rows = math.ceil(n / cols)

    col_w = [
        max(sizes[normal_names[r * cols + c]][0] for r in range(rows) if r * cols + c < n)
        for c in range(cols)
    ]
    row_h = [
        max(sizes[normal_names[r * cols + c]][1] for c in range(cols) if r * cols + c < n)
        for r in range(rows)
    ]
    grid_w = sum(col_w) + (cols - 1) * _SF_GAP
    grid_h = sum(row_h) + (rows - 1) * _SF_GAP

    sink_extra_w = 0
    if sink_list:
        max_sink_w = max(sizes[sn][0] for sn in sink_list)
        sink_extra_w = _SF_GAP + max_sink_w

    total_w = 2 * _SF_PADDING + grid_w + sink_extra_w
    total_h = _SF_HEADER_H + 2 * _SF_PADDING + grid_h
    return (total_w, total_h)


def _compute_sf_layout(states_dict: Dict, origin_x: int = 20, origin_y: int = 20,
                        path_prefix: str = '', transitions=None) -> Dict[str, tuple]:
    """Recursively compute {dotted.path: (x, y, w, h)} for all states.

    Fault/error sink states (FAULT_ACTIVE, etc.) that have no outgoing transitions
    to siblings are placed as a tall sidebar column on the right side of the
    container, spanning the full height of the normal-child grid.
    """
    result: Dict[str, tuple] = {}
    names = list(states_dict.keys())
    n = len(names)
    if n == 0:
        return result

    sink_names = _find_sink_states(states_dict, transitions or [], path_prefix) if transitions is not None else set()
    if transitions is not None:
        normal_names = _bfs_order(states_dict, transitions, path_prefix, sink_names)
    else:
        normal_names = [name for name in names if name not in sink_names]
    sink_list = [name for name in names if name in sink_names]
    if not normal_names:
        normal_names = names
        sink_list = []

    def child_prefix(name):
        return f'{path_prefix}.{name}' if path_prefix else name

    sizes = {name: _sf_state_size(states_dict[name], transitions, child_prefix(name))
             for name in names}

    n_norm = len(normal_names)
    cols = 1 if n_norm > _SF_VERT_THRESH else min(n_norm, _SF_MAX_COLS)
    rows = math.ceil(n_norm / cols)

    col_w = [
        max(sizes[normal_names[r * cols + c]][0] for r in range(rows) if r * cols + c < n_norm)
        for c in range(cols)
    ]
    row_h = [
        max(sizes[normal_names[r * cols + c]][1] for c in range(cols) if r * cols + c < n_norm)
        for r in range(rows)
    ]
    grid_w = sum(col_w) + (cols - 1) * _SF_GAP
    grid_h = sum(row_h) + (rows - 1) * _SF_GAP

    for idx, name in enumerate(normal_names):
        r, c = divmod(idx, cols)
        x = origin_x + sum(col_w[:c]) + c * _SF_GAP
        y = origin_y + sum(row_h[:r]) + r * _SF_GAP
        w, h = sizes[name]
        full_path = child_prefix(name)
        result[full_path] = (x, y, w, h)
        children = states_dict[name].get('states', {})
        if children:
            result.update(_compute_sf_layout(
                children,
                origin_x=x + _SF_PADDING,
                origin_y=y + _SF_HEADER_H + _SF_PADDING,
                path_prefix=full_path,
                transitions=transitions,
            ))

    # Place sink states as full-height sidebar on the right
    sink_x = origin_x + grid_w + _SF_GAP
    for name in sink_list:
        w, _ = sizes[name]
        full_path = child_prefix(name)
        result[full_path] = (sink_x, origin_y, w, grid_h)
        sink_x += w + _SF_GAP

    return result


def _rebuild_state_label(name: str, actions: Dict[str, str]) -> str:
    """Reconstruct the Stateflow LabelString for a state from its name and actions dict."""
    parts = [name]
    for kw in ('en', 'du', 'ex'):
        code = actions.get(kw, '').strip()
        if code:
            parts.append(f'{kw}:\n{code}')
    _LAYOUT_KEYS = {'role', 'default', 'state_type', 'subchart', 'history'}
    for kw, code in actions.items():
        if kw not in ('en', 'du', 'ex') and kw not in _LAYOUT_KEYS and isinstance(code, str) and code.strip():
            parts.append(f'{kw}:\n{code.strip()}')
    return '\n'.join(parts)


_LABEL_WRAP_LEN = 50  # wrap to two lines when trigger+condition+action exceeds this

def _rebuild_transition_label(trigger: str, condition: str, action: str) -> str:
    """Reconstruct a Stateflow transition LabelString from parsed fields.

    Action type is signalled by a leading '/' in the action string:
      action starting with '/'  → transition action  [cond]/action
      action with no prefix     → condition action   [cond]{action}  (default, safe at junctions)

    Labels longer than _LABEL_WRAP_LEN are split at the action boundary:
      [count(~devOnline)>startupTout]
      {dev_fault=DevFault_e.FAULT_LINK_TOUT}
    """
    prefix_parts = []
    if trigger:
        prefix_parts.append(trigger)
    if condition:
        prefix_parts.append(f'[{condition}]')
    prefix = ''.join(prefix_parts)

    if not action:
        return prefix

    action_str = action if action.startswith('/') else f'{{{action}}}'
    sep = '\n' if prefix and len(prefix) + len(action_str) > _LABEL_WRAP_LEN else ''
    return prefix + sep + action_str


def _escape_matlab_str(s: str) -> str:
    """Escape a string for use inside MATLAB single-quoted string literals."""
    return s.replace("'", "''")


def _sf_type_method(type_str: str) -> str:
    """Return the Stateflow Props.Type.Method string for a given type.

    Valid MATLAB values (R2024a): 'Inherited', 'Built-in', 'Bus Object',
    'Enumerated', 'Expression', 'Fixed point'.
    """
    if type_str.startswith('Enum:'):
        return 'Enumerated'
    if type_str.startswith('Bus:'):
        return 'Bus Object'
    if type_str.startswith('Inherit:'):
        return 'Inherited'
    if type_str.startswith('fixdt') or type_str.startswith('sfix') or type_str.startswith('ufix'):
        return 'Fixed point'
    return 'Built-in'


def _matlab_initial_value(v) -> str:
    """Convert a YAML initial_value to a MATLAB Props.InitialValue string.

    MATLAB expects lowercase 'true'/'false' for boolean data; Python's bool
    repr produces 'True'/'False' which MATLAB cannot evaluate.
    """
    if isinstance(v, bool):
        return 'true' if v else 'false'
    return _escape_matlab_str(str(v))


_MATLAB_TYPE_CAST = {
    'boolean': 'logical', 'uint8': 'uint8', 'uint16': 'uint16',
    'uint32': 'uint32', 'uint64': 'uint64', 'int8': 'int8',
    'int16': 'int16', 'int32': 'int32', 'int64': 'int64',
    'single': 'single', 'double': 'double',
}


def _matlab_param_workspace_assign(name: str, type_str, value) -> str:
    """Return a MATLAB base-workspace assignment line for a Parameter value.

    Parameter-scope Stateflow data does not accept Props.InitialValue — the
    value is resolved from the base workspace at simulation time.  Emit a
    typed assignment so the variable is ready before 'sim()' is called.
    """
    cast = _MATLAB_TYPE_CAST.get(str(type_str or '').lower(), '') if type_str else ''

    if isinstance(value, list):
        elems = ' '.join(_matlab_initial_value(v) for v in value)
        val_expr = f'[{elems}]'
    elif isinstance(value, bool):
        val_expr = 'true' if value else 'false'
    else:
        val_expr = str(value)

    if cast:
        val_expr = f'{cast}({val_expr})'

    return f"{name} = {val_expr};"


def _matlab_str_literal(s: str) -> str:
    """Return a MATLAB expression for string s.

    Multi-line strings use sprintf('...\\n...') so MATLAB parses them correctly.
    Single-line strings use plain 'value' notation.

    Inside sprintf, '%' is a format-specifier prefix and must be doubled to '%%'.
    This matters for MATLAB function bodies that contain comment lines (% ...).
    """
    escaped = _escape_matlab_str(s)
    if '\n' in escaped:
        sprintf_body = escaped.replace('%', '%%').replace('\n', '\\n')
        return "sprintf('" + sprintf_body + "')"
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
    dst_pos: 'tuple | None' = None,
) -> None:
    """Emit a Stateflow default transition (no source) pointing to dst_var.

    dst_pos: (x, y, w, h) of the destination state in the coordinate space that
    Stateflow uses for SourceEndPoint on this transition — chart-absolute for
    non-subchart transitions, subchart-relative for transitions inside a subchart.
    When provided, places the dot 20 px above the destination state's top edge so
    it renders inside the parent container rather than at the canvas top-left.
    """
    counter[0] += 1
    tv = f't{counter[0]}'
    if is_auto:
        lines.append('% AUTO-DEFAULT (no default child in YAML; using first child)')
    lines.append(f"{tv} = Stateflow.Transition({parent_var});")
    lines.append(f"{tv}.Destination = {dst_var};")
    lines.append(f"{tv}.DestinationOClock = 0;")
    if dst_pos is not None:
        x, y, w, h = dst_pos
        dot_x = x + w // 2
        dot_y = max(y - _DEFAULT_TRANS_OFFSET, 0)
        mid_y = (dot_y + y) // 2
        lines.append(f"{tv}.SourceEndPoint = [{dot_x} {dot_y}];")
        lines.append(f"{tv}.MidPoint = [{dot_x} {mid_y}];")


def _sf_states_to_matlab_lines(
    states_dict: Dict,
    parent_var: str,
    path_prefix: str,
    counter: List[int],
    path_to_var: Dict[str, str],
    lines: List[str],
    positions: Dict[str, tuple],
    _parent_is_and: bool = False,
    _subchart_path: str = '',
) -> None:
    """Recursively emit MATLAB lines that create Stateflow states.

    _parent_is_and: when True, skip default-transition emission at this level.
    AND states have all regions simultaneously active — no single entry arrow.

    _subchart_path: path of the nearest enclosing subchart (IsSubchart=true).
    Inside a subchart all Position values must be subchart-absolute — i.e. relative
    to the subchart's own origin — regardless of how deep the state is nested.
    At chart level (no enclosing subchart) Position is parent-relative as usual.
    """
    default_child_name = None
    has_explicit_default = False
    if not _parent_is_and:
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

        _is_junction = bool(state_body.get('junction'))
        actions = {k: v for k, v in state_body.items()
                   if k not in ('states', 'default', 'type', 'subchart', 'history', 'junction', 'desc', 'req', 'role') and isinstance(v, str)}

        if _is_junction:
            lines.append(f"{var} = Stateflow.Junction({parent_var});")
            if full_path in positions:
                x, y, w, h = positions[full_path]
                if _subchart_path and _subchart_path in positions:
                    px, py = positions[_subchart_path][0], positions[_subchart_path][1]
                else:
                    px, py = 0, 0
                cx = (x - px) + w // 2
                cy = (y - py) + h // 2
                lines.append(f"{var}.Position.Center = [{cx} {cy}];")
                lines.append(f"{var}.Position.Radius = 10;")
        else:
            label = _rebuild_state_label(state_name, actions)
            lines.append(f"{var} = Stateflow.State({parent_var});")
            lines.append(f"{var}.Name = '{_escape_matlab_str(state_name)}';")
            lines.append(f"{var}.LabelString = {_matlab_str_literal(label)};")
            _desc = _format_description(state_body.get('desc'), state_body.get('req'))
            if _desc:
                lines.append(f"{var}.Description = {_matlab_str_literal(_desc)};")
            if full_path in positions:
                x, y, w, h = positions[full_path]
                # Stateflow.State.Position is always chart-absolute for non-subchart states.
                # Only states that are direct children of a subchart use subchart-relative coords.
                # All other states (chart-level AND children of non-subchart compounds) must use
                # chart-absolute coords so Stateflow places them correctly in the visual hierarchy.
                if _subchart_path and _subchart_path in positions:
                    px, py = positions[_subchart_path][0], positions[_subchart_path][1]
                else:
                    px, py = 0, 0
                lines.append(f"{var}.Position = [{x - px} {y - py} {w} {h}];")

            if state_body.get('subchart'):
                lines.append(f"{var}.IsSubchart = true;")
                lines.append(f"{var}.ContentPreviewEnabled = false;")

            if state_body.get('type') == 'AND':
                lines.append(f"{var}.Decomposition = 'PARALLEL_AND';")

        if state_name == default_child_name:
            dst_abs = positions.get(full_path)
            if dst_abs is not None and _subchart_path and _subchart_path in positions:
                sc_x, sc_y = positions[_subchart_path][0], positions[_subchart_path][1]
                dst_coord = (dst_abs[0] - sc_x, dst_abs[1] - sc_y, dst_abs[2], dst_abs[3])
            else:
                dst_coord = dst_abs
            _emit_sf_default_transition(var, parent_var, counter, lines,
                                        is_auto=not has_explicit_default,
                                        dst_pos=dst_coord)

        children = state_body.get('states', {})
        if children and not _is_junction:
            if state_body.get('history'):
                # History junction floats freely inside the state — no transitions connect to it.
                # Stateflow's engine uses its presence to restore the last active substate on re-entry.
                counter[0] += 1
                hjv = f'j{counter[0]}'
                # Use the actual content right-edge rather than the display width, which may
                # differ when __subchart_leaf_size__ compresses the parent-level footprint.
                _sc_ox = positions.get(full_path, (0, 0, 0, 0))[0]
                _sc_pfx = full_path + '.'
                _content_right = max(
                    (pos[0] - _sc_ox + pos[2]
                     for k, pos in positions.items() if k.startswith(_sc_pfx)),
                    default=0,
                )
                _state_w = _content_right if _content_right > 0 else positions.get(full_path, (0, 0, 0, 0))[2]
                _hjc_x = (_state_w - _SF_PADDING - 10) if _state_w > 60 else (_SF_PADDING + 10)
                _hjc_y = _SF_HEADER_H + _SF_PADDING + 10
                lines += [
                    f"{hjv} = Stateflow.Junction({var});",
                    f"{hjv}.Type = 'HISTORY';",
                    f"{hjv}.Position.Center = [{_hjc_x} {_hjc_y}];",
                    f"{hjv}.Position.Radius = 10;",
                ]
            # If this state is a subchart it becomes the coordinate origin for all descendants.
            new_subchart_path = full_path if state_body.get('subchart') else _subchart_path
            _sf_states_to_matlab_lines(children, var, full_path, counter, path_to_var,
                                       lines, positions,
                                       _parent_is_and=(state_body.get('type') == 'AND'),
                                       _subchart_path=new_subchart_path)


def stateflow_dict_to_matlab(chart_dict: Dict, model_name: 'str | None' = None,
                             export_charts: bool = False,
                             elk_options: 'Dict | None' = None) -> str:
    """Generate a MATLAB .m script that recreates a Stateflow chart from a chart dict.

    chart_dict should be the output of stateflow_chart_to_dict().
    model_name defaults to the chart name with spaces replaced by underscores.
    export_charts: if True, appends inline MATLAB code to export all charts to PNG.
    elk_options: optional ELK layout option overrides, e.g. {'elk.direction': 'RIGHT'}.
    """
    chart_name = chart_dict.get('name', 'Chart')
    if model_name is None:
        model_name = re.sub(r'[^\w]', '_', chart_name)

    lines: List[str] = []
    lines.append(f'%% Generated by slxgen - recreates Stateflow chart: {chart_name}')
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
    action_lang = chart_dict.get('language', 'MATLAB').upper()
    lines.append(f"ch.ActionLanguage = '{_escape_matlab_str(action_lang)}';")

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
            lines.append(f"{v}.Props.Type.Method = '{_sf_type_method(d['type'])}';")
            lines.append(f"{v}.DataType = '{_escape_matlab_str(d['type'])}';")
        if d.get('initial_value') is not None:
            lines.append(f"{v}.Props.InitialValue = '{_matlab_initial_value(d['initial_value'])}';")
        if 'size' in d:
            size_str = ' '.join(str(n) for n in d['size'])
            lines.append(f"{v}.Props.Array.Size = '[{size_str}]';")

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
            lines.append(f"{v}.Props.Type.Method = '{_sf_type_method(d['type'])}';")
            lines.append(f"{v}.DataType = '{_escape_matlab_str(d['type'])}';")
        if d.get('initial_value') is not None:
            lines.append(f"{v}.Props.InitialValue = '{_matlab_initial_value(d['initial_value'])}';")
        if 'size' in d:
            size_str = ' '.join(str(n) for n in d['size'])
            lines.append(f"{v}.Props.Array.Size = '[{size_str}]';")

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
            lines.append(f"{v}.Props.Type.Method = '{_sf_type_method(d['type'])}';")
            lines.append(f"{v}.DataType = '{_escape_matlab_str(d['type'])}';")
        if d.get('initial_value') is not None:
            lines.append(f"{v}.Props.InitialValue = '{_matlab_initial_value(d['initial_value'])}';")
        if 'size' in d:
            size_str = ' '.join(str(n) for n in d['size'])
            lines.append(f"{v}.Props.Array.Size = '[{size_str}]';")

    params = chart_dict.get('params', [])
    if params:
        lines.append('')
        lines.append('%% Parameters')
    for i, d in enumerate(params, 1):
        v = f'd_par{i}'
        lines.append(f"{v} = Stateflow.Data(ch);")
        lines.append(f"{v}.Name = '{_escape_matlab_str(d['name'])}';")
        lines.append(f"{v}.Scope = 'Parameter';")
        if d.get('type'):
            lines.append(f"{v}.Props.Type.Method = '{_sf_type_method(d['type'])}';")
            lines.append(f"{v}.DataType = '{_escape_matlab_str(d['type'])}';")
        if 'size' in d:
            size_str = ' '.join(str(n) for n in d['size'])
            lines.append(f"{v}.Props.Array.Size = '[{size_str}]';")
        # Parameter scope data does not support Props.InitialValue — the value
        # is resolved from the base workspace by name at simulation time.
        if d.get('initial_value') is not None:
            lines.append(_matlab_param_workspace_assign(d['name'], d.get('type'), d['initial_value']))

    states_dict = chart_dict.get('states', {})
    # Preserve original YAML order for ELK (edge model-order affects LINEAR_SEGMENTS placement).
    # Sort by (from-path, order) only for MATLAB emission so Stateflow assigns ExecutionOrder
    # in the correct sequence.  orig_idx is the index used for EDGE||…||{idx} IDs in ELK.
    _transitions_raw = list(enumerate(chart_dict.get('transitions', [])))  # [(orig_idx, tr), ...]
    _transitions_emit = sorted(
        _transitions_raw,
        key=lambda it: (it[1].get('from', ''), int(it[1].get('order', '0')))
    )
    transitions = [t for _, t in _transitions_raw]   # original order — for ELK

    counter: List[int] = [0]
    path_to_var: Dict[str, str] = {}
    positions: Dict[str, tuple] = {}
    edge_routing: dict = {}
    sink_junctions: dict = {}
    orthogonal_junctions: bool = False
    bare_transitions: bool = False
    if states_dict:
        if _ELK_AVAILABLE:
            try:
                _elk_opts  = dict(elk_options) if elk_options else {}
                _max_lw    = _elk_opts.pop('__max_label_width__',    None)
                _label_sub = _elk_opts.pop('__label_substitution__', None)
                _dir       = _elk_opts.pop('__direction__',           None)
                _fbus      = _elk_opts.pop('__sink_bus_junctions__', None)
                _ortho     = _elk_opts.pop('__orthogonal_junctions__', None)
                _bare_tr   = _elk_opts.pop('__bare_transitions__',     None)
                _sp        = _elk_opts.pop('__sink_placement__',       None)
                _no_fp     = _elk_opts.pop('__no_sink_placement__',    None)  # backward compat
                _auto_sink = _elk_opts.pop('__auto_sink__',            None)
                _sc_leaf   = _elk_opts.pop('__subchart_leaf_size__',   None)
                _dump_elk  = _elk_opts.pop('__dump_elk_dir__',         None)
                _adapt_lw  = _elk_opts.pop('__adaptive_leaf_width__',  None)
                _adapt_sp  = _elk_opts.pop('__adaptive_spacing__',     None)
                _elk_kw: dict = {}
                if _max_lw    is not None: _elk_kw['max_label_width']    = int(_max_lw)
                if _label_sub is not None: _elk_kw['label_substitution'] = bool(_label_sub)
                if _dir       is not None: _elk_kw['direction']          = str(_dir)
                if _adapt_lw  is not None: _elk_kw['adaptive_leaf_width'] = bool(_adapt_lw)
                if _adapt_sp  is not None: _elk_kw['adaptive_spacing']    = bool(_adapt_sp)
                auto_sinks: 'frozenset[str]' = frozenset()
                if _auto_sink is not None:
                    _min = int(_auto_sink) if _auto_sink.isdigit() else 2
                    auto_sinks = _compute_auto_sinks(states_dict, transitions, _min)
                sink_placement = 'none'
                if _sp is not None:
                    sink_placement = _sp.lower().strip()
                elif _no_fp is not None and _no_fp.lower() in ('0', 'false', 'no'):
                    sink_placement = 'right'  # __no_sink_placement__: false → right (backward compat)
                _fbus_flag = _fbus is not None and _fbus.lower() in ('1', 'true', 'yes')
                sc_leaf_size: 'tuple | None' = None
                if _sc_leaf is not None:
                    _m = re.match(r'(\d+)[x,\s]+(\d+)', _sc_leaf.strip(), re.I)
                    if _m:
                        sc_leaf_size = (int(_m.group(1)), int(_m.group(2)))
                    elif _sc_leaf.strip().lower() in ('1', 'true', 'yes'):
                        sc_leaf_size = (200, 150)   # compact standard size
                positions, edge_routing, sink_junctions = elk_layout_bottomup(
                    {'states': states_dict, 'transitions': transitions},
                    layout_options=_elk_opts, auto_sinks=auto_sinks,
                    sink_bus_junctions=_fbus_flag, sink_placement=sink_placement,
                    subchart_leaf_size=sc_leaf_size, dump_dir=_dump_elk,
                    **_elk_kw)
                orthogonal_junctions = _ortho is not None and _ortho.lower() in ('1', 'true', 'yes')
                bare_transitions     = _bare_tr is not None and _bare_tr.lower() in ('1', 'true', 'yes')
            except Exception:
                positions = _compute_sf_layout(states_dict, transitions=transitions)
        else:
            positions = _compute_sf_layout(states_dict, transitions=transitions)
        lines.append('')
        lines.append('%% States')
        _sf_states_to_matlab_lines(states_dict, 'ch', '', counter, path_to_var, lines, positions)

    # --- Junction pre-pass: emit Stateflow.Junction nodes and spine connector transitions
    # Must run AFTER state emission (path_to_var is now populated) and BEFORE transition loop.
    junction_vars: Dict[str, Dict] = {}  # {fault_path: {'entries': [jv,...], 'gateway': jv}}
    if sink_junctions:
        lines.append('')
        lines.append('%% Sink-bus junctions')
    for fault_path, bus in sink_junctions.items():
        parent_var = path_to_var.get(bus['parent'], 'ch')
        px, py     = positions.get(bus['parent'], (0, 0, 0, 0))[:2]

        entry_jvars: List[str] = []
        entry_jys: List[int] = []   # absolute y for each entry junction (fan OClock direction)
        for entry in bus['entries']:
            counter[0] += 1
            jv = f"j{counter[0]}"
            lines += [
                f"{jv} = Stateflow.Junction({parent_var});",
                f"{jv}.Position.Center = [{bus['spine_x'] - px} {entry['jy'] - py}];",
                f"{jv}.Position.Radius = 5;",
            ]
            entry_jvars.append(jv)
            entry_jys.append(entry['jy'])

        if bus['gateway_y'] is not None:
            counter[0] += 1
            gv = f"j{counter[0]}"
            gw_y = bus['gateway_y']
            lines += [
                f"{gv} = Stateflow.Junction({parent_var});",
                f"{gv}.Position.Center = [{bus['spine_x'] - px} {gw_y - py}];",
                f"{gv}.Position.Radius = 5;",
            ]
        else:
            gv = entry_jvars[bus['gateway_entry_idx']]  # closest entry is the gateway
            gw_y = entry_jys[bus['gateway_entry_idx']]

        junction_vars[fault_path] = {'entries': entry_jvars, 'gateway': gv}

        # Fan topology: each non-gateway entry connects directly to the gateway junction.
        # This avoids V-shaped chains when the gateway y-level is between entries.
        for ev, ey in zip(entry_jvars, entry_jys):
            if ev == gv:
                continue
            counter[0] += 1
            tv = f"t{counter[0]}"
            lines += [
                f"{tv} = Stateflow.Transition({parent_var});",
                f"{tv}.Source = {ev};",
                f"{tv}.Destination = {gv};",
            ]
            if orthogonal_junctions:
                # Straight vertical spine: same x, just exit toward the gateway
                src_oc = 6 if ey < gw_y else 12  # exit bottom if above, top if below
                lines.append(f"{tv}.SourceOClock = {src_oc};")

        # Gateway → fault state (horizontal entry from left)
        fault_var = path_to_var.get(fault_path, '')
        if fault_var:
            counter[0] += 1
            tv = f"t{counter[0]}"
            lines += [
                f"{tv} = Stateflow.Transition({parent_var});",
                f"{tv}.Source = {gv};",
                f"{tv}.Destination = {fault_var};",
                f"{tv}.DestinationOClock = 9;",
            ]
            if orthogonal_junctions:
                lines.append(f"{tv}.SourceOClock = 3;")

    if _transitions_emit:
        lines.append('')
        lines.append('%% Transitions')
    _stagger_used: dict = {}  # {(lca, mid_x_bucket): [mid_y]} — keyed by LCA + x-zone so
    # arcs in different visual lanes (normal left, fault right) don't stagger against each other
    for tr_idx, tr in _transitions_emit:  # tr_idx == original YAML index (matches ELK edge IDs)
        counter[0] += 1
        tv = f't{counter[0]}'
        src_path = tr.get('from', '')
        dst_path = tr.get('to', '')
        src_var = path_to_var.get(src_path, '')
        dst_var = path_to_var.get(dst_path, '')
        lca = _lca_path(src_path, dst_path)
        # Self-transition: _lca_path returns the state itself; parent must be the
        # containing compound state so execution orders are in the same scope as
        # the other outgoing transitions and sfLintChart does not flag [1 1].
        if src_path and src_path == dst_path:
            parts = src_path.split('.')
            lca = '.'.join(parts[:-1])  # e.g. "CONTROL.AUTO" → "CONTROL"
        tr_parent_var = path_to_var.get(lca, 'ch') if lca else 'ch'
        label = _rebuild_transition_label(
            tr.get('trigger', ''), tr.get('condition', ''), tr.get('action', '')
        )

        # --- Fault-bus junction routing: reroute src → entry_junction instead of src → fault
        if dst_path in junction_vars and src_path in positions:
            jbus   = sink_junctions[dst_path]
            jvinfo = junction_vars[dst_path]
            idx    = next((i for i, e in enumerate(jbus['entries']) if e['src'] == src_path), None)
            if idx is not None:
                entry_jv = jvinfo['entries'][idx]
                lca_x, lca_y = (positions[lca][0], positions[lca][1]) if lca and lca in positions else (0, 0)
                sx, sy, sw, sh = positions[src_path]
                mid_x = (sx + sw - lca_x + jbus['spine_x'] - lca_x) // 2
                mid_y = sy + sh // 2 - lca_y
                _tr_desc = _format_description(tr.get('desc'), tr.get('req'))
                lines.append(f"{tv} = Stateflow.Transition({tr_parent_var});")
                if src_var:
                    lines.append(f"{tv}.Source = {src_var};")
                lines.append(f"{tv}.Destination = {entry_jv};")
                if _tr_desc:
                    lines.append(f"{tv}.Description = {_matlab_str_literal(_tr_desc)};")
                if label:
                    lines.append(f"{tv}.LabelString = {_matlab_str_literal(label)};")
                if tr.get('order'):
                    lines.append(f"{tv}.ExecutionOrder = {tr['order']};")
                lines.append(f"{tv}.SourceOClock = 3;")
                if orthogonal_junctions:
                    lines.append(f"{tv}.DestinationOClock = 9;")
                lines.append(f"{tv}.MidPoint = [{mid_x} {mid_y}];")
                if label:
                    lj, ly = mid_x, mid_y
                    key  = (lca, min(src_path, dst_path), max(src_path, dst_path))
                    used = _stagger_used.setdefault(key, [])
                    ly = _stagger_label_y(ly, None, None, used, _SF_LABEL_STAGGER)
                    used.append(ly)
                    lw = min(max(int(len(label) * _SF_PX_PER_CHAR) + 20, 60), 300)
                    lines.append(f"{tv}.LabelPosition = [{lj - lw // 2} {ly - _SF_LABEL_LINE_H // 2} {lw} {_SF_LABEL_LINE_H}];")
                continue  # skip normal emit for this transition

        # --- Normal emit
        _tr_desc = _format_description(tr.get('desc'), tr.get('req'))
        lines.append(f"{tv} = Stateflow.Transition({tr_parent_var});")
        if src_var:
            lines.append(f"{tv}.Source = {src_var};")
        else:
            lines.append(f"% WARNING: source state '{src_path}' not found")
        if dst_var:
            lines.append(f"{tv}.Destination = {dst_var};")
        else:
            lines.append(f"% WARNING: destination state '{dst_path}' not found")
        if _tr_desc:
            lines.append(f"{tv}.Description = {_matlab_str_literal(_tr_desc)};")
        if label:
            lines.append(f"{tv}.LabelString = {_matlab_str_literal(label)};")
        if tr.get('order'):
            lines.append(f"{tv}.ExecutionOrder = {tr['order']};")
        if not bare_transitions and src_path in positions and dst_path in positions:
            sx, sy, sw, sh = positions[src_path]
            dx, dy, dw, dh = positions[dst_path]
            edge_id = f'EDGE||{src_path}||{dst_path}||{tr_idx}'
            lca_x, lca_y = (positions[lca][0], positions[lca][1]) if lca and lca in positions else (0, 0)
            if edge_id in edge_routing:
                er = edge_routing[edge_id]
                mid_x = er['mid_x']
                mid_y = er['mid_y']
                lines.append(f"{tv}.MidPoint = [{mid_x} {mid_y}];")
                if label:
                    lx, ly = _push_label_outside_states(mid_x, mid_y, positions, src_path, dst_path)
                    y_lo, y_hi = _label_gap_bounds(src_path, dst_path, positions)
                    key  = (lca, min(src_path, dst_path), max(src_path, dst_path))
                    used = _stagger_used.setdefault(key, [])
                    ly = _stagger_label_y(ly, y_lo, y_hi, used, _SF_LABEL_STAGGER)
                    used.append(ly)
                    lw = min(max(int(len(label) * _SF_PX_PER_CHAR) + 20, 60), 300)
                    lines.append(f"{tv}.LabelPosition = [{max(0, lx - lw // 2)} {ly - _SF_LABEL_LINE_H // 2} {lw} {_SF_LABEL_LINE_H}];")
                lines.append(f"{tv}.SourceOClock = {er['src_oclock']};")
                lines.append(f"{tv}.DestinationOClock = {er['dst_oclock']};")
            else:
                # Fallback: simple side routing
                src_cx = sx + sw // 2
                dst_cx = dx + dw // 2
                if src_path == dst_path:
                    # Self-loop: arc above the state, entering/exiting at 1/11 o'clock
                    mid_x = sx + sw // 2
                    mid_y = sy - 40
                    lines.append(f"{tv}.MidPoint = [{mid_x - lca_x} {mid_y - lca_y}];")
                    lines.append(f"{tv}.SourceOClock = 1;")
                    lines.append(f"{tv}.DestinationOClock = 11;")
                else:
                    mid_x  = (src_cx + dst_cx) // 2
                    mid_y  = ((sy + sh // 2) + (dy + dh // 2)) // 2
                    lines.append(f"{tv}.MidPoint = [{mid_x - lca_x} {mid_y - lca_y}];")
                    if src_cx <= dst_cx:
                        lines.append(f"{tv}.SourceOClock = 3;")
                        lines.append(f"{tv}.DestinationOClock = 9;")
                    else:
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
    lines.append('%% Diagnostics - compile the diagram to surface Stateflow errors')
    lines.append('try')
    lines.append("    set_param(model_name, 'SimulationCommand', 'update');")
    lines.append("    disp('Diagram update: OK');")
    lines.append('catch e')
    lines.append("    fprintf('Diagram update errors:\\n%s\\n', e.message);")
    lines.append('end')

    if export_charts:
        lines.append('')
        lines.append('%% Export chart and subchart images to PNG')
        lines.append('try')
        lines.append('    rt_exp = sfroot;')
        lines.append("    m_exp  = rt_exp.find('-isa', 'Stateflow.Machine', 'Name', model_name);")
        lines.append("    charts_exp = m_exp.find('-isa', 'Stateflow.Chart');")
        lines.append('    for i_exp = 1:numel(charts_exp)')
        lines.append("        safe_name = regexprep(charts_exp(i_exp).Path, '[/\\\\:*?\"<>| ]', '_');")
        lines.append("        out_png = fullfile(pwd, [safe_name '.png']);")
        lines.append("        sfprint(charts_exp(i_exp), 'png', out_png);")
        lines.append("        fprintf('  [chart]    -> %s\\n', out_png);")
        lines.append('    end')
        lines.append("    sub_exp = m_exp.find('-isa', 'Stateflow.State', 'IsSubchart', true);")
        lines.append('    for i_exp = 1:numel(sub_exp)')
        lines.append("        sc_path  = [sub_exp(i_exp).Path '/' sub_exp(i_exp).Name];")
        lines.append("        safe_name = regexprep(sc_path, '[/\\\\:*?\"<>| ]', '_');")
        lines.append("        out_png = fullfile(pwd, [safe_name '.png']);")
        lines.append("        sfprint(sub_exp(i_exp), 'png', out_png);")
        lines.append("        fprintf('  [subchart] -> %s\\n', out_png);")
        lines.append('    end')
        lines.append('catch e_exp')
        lines.append("    fprintf('Chart export failed: %s\\n', e_exp.message);")
        lines.append('end')

    return '\n'.join(lines) + '\n'


def sf_yaml_to_matlab(yaml_path, output_path=None, model_name: 'str | None' = None,
                      export_charts: bool = False,
                      elk_options: 'Dict | None' = None,
                      default_size: 'List | None' = None) -> str:
    """Read a Stateflow sf.yaml file and generate a MATLAB script to recreate it.

    Returns the script as a string. If output_path is given, also writes it to disk.
    model_name: override the model/chart name from the YAML (useful for side-by-side comparisons).
    export_charts: if True, the generated script includes inline PNG export at the end.
    elk_options: optional ELK layout option overrides, e.g. {'elk.direction': 'RIGHT'}.
    default_size: size applied to variables with no explicit size: field.
        None / [1] → scalar (default). [-1] → inherited from connected signal.
    """
    chart_dict = yaml.safe_load(Path(yaml_path).read_text(encoding='utf-8'))
    sir = yaml_to_sir(chart_dict, default_size=default_size)
    issues = sir_validate(sir)
    if issues:
        label = Path(yaml_path).name
        for msg in issues:
            print(f"[SIR:{label}] {msg}", file=sys.stderr)
    chart_dict = sir_to_chart_dict(sir)
    script = stateflow_dict_to_matlab(chart_dict, model_name=model_name,
                                      export_charts=export_charts, elk_options=elk_options)
    if output_path:
        Path(output_path).write_text(script, encoding='utf-8')
    return script
