import xml.etree.ElementTree as ET
import json
import re
import yaml
import zipfile
from pathlib import Path
from typing import Dict, List, Any
from collections import defaultdict

from .stateflow import (
    load_stateflow_machine,
    parse_stateflow_chart,
    _sf_render,
    _SFYamlDumper,
    stateflow_chart_to_dict,
    _collect_sf_charts,
    stateflow_dict_to_matlab,
    sf_yaml_to_matlab,
)


# ----------------------------------------------------------------------
# 1. Parsing – build port maps for Inport / Outport blocks
# ----------------------------------------------------------------------
def _build_port_map(blocks: Dict[int, Dict]) -> tuple[Dict[int, str], Dict[int, str]]:
    """Return (input_port_map, output_port_map) – port_number → name."""
    in_map: Dict[int, str] = {}
    out_map: Dict[int, str] = {}

    inports  = [b for b in blocks.values() if b['type'] == 'Inport']
    outports = [b for b in blocks.values() if b['type'] == 'Outport']

    # ----- INPUT ports -------------------------------------------------
    used = set()
    for blk in inports:
        p = blk['parameters'].get('Port')
        if p:
            num = int(p)
            if num not in used:
                in_map[num] = blk['name']
                used.add(num)
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
            'inputs': defaultdict(lambda: defaultdict(list)),
            'outputs': defaultdict(lambda: defaultdict(list)),
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
    for blk in blocks.values():
        if 'subsystem' in blk:
            sub_model = blk['subsystem']
            in_map, out_map = _build_port_map(sub_model['blocks'])
            blk['input_ports'] = in_map
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
    if ':' not in rest:
        direction = rest
        port_str = '0'
    else:
        direction, port_str = rest.split(':', 1)
    return sid_str, direction, int(port_str)


def enrich_connections(model: Dict) -> Dict:
    """Recursively enrich *all* levels (root + every subsystem)."""
    _enrich_one_level3(model)

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
    real_sources: Dict[int, List[tuple[int, int]]] = defaultdict(list)
    goto_incoming_links = set()

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
    real_destinations: Dict[int, List[tuple[int, int]]] = defaultdict(list)
    from_outgoing_links = set()

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

            if src_blk['type'] in ('Goto', 'From') or dst_blk['type'] in ('Goto', 'From'):
                continue

            real_links.append([src_sid, src_port, dst_sid, dst_port])

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
                        virtual_resolved_links.append([src_sid, src_port, dst_sid, dst_port])

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
    model['connection_links'] = real_links
    model['resolved_links'] = real_links + virtual_resolved_links

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
    skip_in: Dict[str, list] = defaultdict(list)
    skip_out: Dict[str, list] = defaultdict(list)

    for src_sid, src_port, dst_sid, dst_port in links:
        if dst_sid in skip_sids:
            skip_in[dst_sid].append((src_sid, src_port, dst_port))
        if src_sid in skip_sids:
            skip_out[src_sid].append((dst_sid, dst_port, src_port))

    stitched = []
    for sid in skip_sids:
        for (src_sid, src_port, in_port) in skip_in[sid]:
            for (dst_sid, dst_port, out_port) in skip_out[sid]:
                if in_port == out_port:
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
        if blk.get('type') == 'Reference':
            source_type = blk.get('parameters', {}).get('SourceType', '')
            if source_type in tm:
                pk.extend(tm[source_type])

        if 'parameters' in blk and blk['parameters']:
            f['parameters'] = {k: v for k, v in blk['parameters'].items() if k in pk}
        if 'instance_data' in blk and blk['instance_data']:
            f['instance_data'] = {k: v for k, v in blk['instance_data'].items() if k in pk}

        _OP_DEFAULTS = {'Logic': 'AND', 'RelationalOperator': '=='}
        btype = blk.get('type', '')
        if btype in _OP_DEFAULTS and 'Operator' in pk:
            f.setdefault('parameters', {}).setdefault('Operator', _OP_DEFAULTS[btype])

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

    skip_sids = {sid for sid, b in all_blocks.items() if b.get('type') in skip}

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

    lines.append(f"\n=== {path} ===")

    if inports:
        names = ' | '.join(_clean(b['name']) for _, b in inports)
        lines.append(f"INPUTS:  {names}")
    if outports:
        names = ' | '.join(_clean(b['name']) for _, b in outports)
        lines.append(f"OUTPUTS: {names}")

    if charts:
        lines.append("CHARTS:")
        for _, b in charts:
            in_p  = _fmt_ports(b.get('input_ports', {}))
            out_p = _fmt_ports(b.get('output_ports', {}))
            ports = ''
            if in_p:  ports += f"  in[{in_p}]"
            if out_p: ports += f"  out[{out_p}]"
            lines.append(f"  {_clean(b['name'])}  [Chart]{ports}")

    if subsystems:
        lines.append("SUBSYSTEMS:")
        for _, b in subsystems:
            in_p  = _fmt_ports(b.get('input_ports', {}))
            out_p = _fmt_ports(b.get('output_ports', {}))
            ports = ''
            if in_p:  ports += f"  in[{in_p}]"
            if out_p: ports += f"  out[{out_p}]"
            lines.append(f"  {_clean(b['name'])}{ports}")

    if disabled:
        lines.append("DISABLED (commented-out, not in code gen):")
        for _, b in disabled:
            btype = b.get('type', '?')
            lines.append(f"  {_clean(b['name'])}  [{btype}]")

    if other:
        lines.append("BLOCKS:")
        for _, b in other:
            btype = b.get('type', '?')
            if btype == 'Reference':
                btype = b.get('parameters', {}).get('SourceType') or btype
            bname = _clean(b.get('name', '?'))
            params = {**b.get('parameters', {}), **b.get('instance_data', {})}
            params.pop('SourceType', None)
            params.pop('SourceBlock', None)
            pstr = ('  ' + '  '.join(f"{k}={_clean(str(v))}" for k, v in params.items())) if params else ''
            lines.append(f"  [{btype}]  {bname}{pstr}")

    if gotos or froms:
        lines.append("VIRTUAL SIGNALS (Goto→From):")
        tags = dict.fromkeys(
            b.get('parameters', {}).get('GotoTag', '') for _, b in gotos)
        for tag in tags:
            g_names = [_clean(b['name']) for _, b in gotos
                       if b.get('parameters', {}).get('GotoTag') == tag]
            f_names = [_clean(b['name']) for _, b in froms
                       if b.get('parameters', {}).get('GotoTag') == tag]
            rhs = ', '.join(f_names) if f_names else '(none)'
            lines.append(f"  {tag:<20}  {', '.join(g_names)} → {rhs}")

    conn = model.get('connection_info', [])
    if conn:
        lines.append("SIGNAL FLOW:")
        for c in conn:
            lines.append(f"  {_clean(c)}")

    sf_texts = []
    for _, b in charts:
        chart_path = f"{path} > {_clean(b['name'])}"
        sf_texts.append(_sf_render(b['stateflow'], chart_path))

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
        if output_dir:
            Path(output_dir).mkdir(parents=True, exist_ok=True)
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
    processed = {}

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
    root_name = next(iter(processed))

    ref_tree = {name: sorted(collect_refs(slim)) for name, slim in processed.items()}

    lines = []
    lines += [
        '# LLM Guide — Simulink Model Reports',
        '',
        'This folder contains exported reports for a Simulink model tree.',
        f'Entry-point model: **{root_name}**',
        '',
    ]

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

    lines += ['## Model Tree', '']
    lines.append(f'- **{root_name}** ← entry point')
    for name in ref_tree:
        if name == root_name:
            continue
        referrers = [p for p, r in ref_tree.items() if name in r]
        lines.append(f'- **{name}** ← referenced by: {", ".join(referrers)}')
    lines.append('')

    lines += [
        '## Suggested Reading Order',
        '',
        f'1. `{root_name}_arch.md` — get the top-level architecture: which subsystems exist and how they connect.',
        f'2. `{root_name}_report.txt` — understand root-level signal routing and which signals feed into each subsystem.',
        '3. For each subsystem of interest, open its own `_report.txt` for detailed block-level analysis.',
        '4. Use `_arch.md` files for any subsystem that has multiple nested levels.',
        '',
    ]

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
        'default_attrs': ['name', 'type', 'input_ports', 'output_ports'],
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
            'EnablePort': ['StatesWhenEnabling'],
        },
        'skip_blocks': ['SignalConversion', 'DataTypeConversion'],
    }
    filein = r'data/model/pid_control_ex1.slx'
    filefull_out = filein.replace('.slx', '_full.json')
    filelim_out = filein.replace('.slx', '_slim.json')
    fileslimmin_out = filein.replace('.slx', '_slim.min.json')
    full = parse_slx(filein)

    full = enrich_connections(full)
    slim = filter_model_data(full, FILTERS)

    with open(filefull_out, 'w', encoding='utf-8') as f:
        json.dump(full, f, indent=2, ensure_ascii=False)
    with open(filelim_out, 'w', encoding='utf-8') as f:
        json.dump(slim, f, indent=2, ensure_ascii=False)

    with open(fileslimmin_out, 'w', encoding='utf-8') as f:
        json.dump(slim, f, separators=(',', ':'), ensure_ascii=False)

    print("\n--- Sample human-readable connections (root) ---")
    for line in slim.get('connection_info', [])[:8]:
        print(line)
