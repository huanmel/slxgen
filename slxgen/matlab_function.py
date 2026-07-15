"""MATLAB Function block generator for slxgen.

Generates a Simulink model containing a MATLAB Function block (Stateflow.EMChart)
from a YAML specification.

YAML format:
    type: matlab_function
    name: MyFilter
    desc: "optional description"

    inputs:
      - {name: u,  type: single}
      - {name: Ts, type: double}

    outputs:
      - {name: y,  type: single}

    params:
      - {name: GAIN, type: single, value: 0.1}

    code: |
      persistent x_prev;
      if isempty(x_prev), x_prev = 0; end
      y = GAIN * u + (1 - GAIN) * x_prev;
      x_prev = y;

Key differences from Stateflow YAML:
  - No states/transitions — just inputs, outputs, params, and a code body
  - params are workspace variables (not function arguments)
  - code: is the function body only (no signature/end — generated automatically)
  - locals: (persistent state) go directly in code: with MATLAB's 'persistent' keyword
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml as _yaml

from .stateflow import (
    _escape_matlab_str,
    _matlab_initial_value,
    _matlab_param_workspace_assign,
    _matlab_str_literal,
    _sf_type_method,
    _MATLAB_TYPE_CAST,
)


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class SIRMatlabFunction:
    name: str
    inputs: list[dict]   # [{name, type?, size?, initial_value?}]
    outputs: list[dict]  # [{name, type?, size?, initial_value?}]
    params: list[dict]   # [{name, type?, size?, value?}]
    code: str            # function body (no signature, no 'end')
    desc: str | None = None
    req: list[str] | None = None


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def yaml_to_mlf(chart_dict: dict) -> SIRMatlabFunction:
    """Parse a YAML dict (type: matlab_function) into SIRMatlabFunction."""
    name = chart_dict.get('name', '')
    desc = chart_dict.get('desc')
    req_raw = chart_dict.get('req')
    req = ([req_raw] if isinstance(req_raw, str) else list(req_raw)) if req_raw else None
    code = str(chart_dict.get('code', ''))

    def _parse_vars(key: str) -> list[dict]:
        out = []
        for v in chart_dict.get(key, []):
            entry: dict[str, Any] = {'name': v.get('name', '')}
            if 'type' in v:
                entry['type'] = v['type']
            if 'size' in v:
                entry['size'] = v['size']
            if 'initial_value' in v:
                entry['initial_value'] = v['initial_value']
            out.append(entry)
        return out

    def _parse_params() -> list[dict]:
        out = []
        for v in chart_dict.get('params', []):
            entry: dict[str, Any] = {'name': v.get('name', '')}
            if 'type' in v:
                entry['type'] = v['type']
            if 'size' in v:
                entry['size'] = v['size']
            # value: or initial_value: both work
            val = v.get('value', v.get('initial_value'))
            if val is not None:
                entry['value'] = val
            out.append(entry)
        return out

    return SIRMatlabFunction(
        name=name,
        inputs=_parse_vars('inputs'),
        outputs=_parse_vars('outputs'),
        params=_parse_params(),
        code=code,
        desc=desc,
        req=req,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def mlf_validate(mlf: SIRMatlabFunction) -> list[str]:
    """Return list of WARNING/ERROR strings for the SIRMatlabFunction."""
    issues: list[str] = []
    if not mlf.name:
        issues.append('ERROR: name is required for matlab_function')
    if not mlf.code.strip():
        issues.append('ERROR: code block is empty')
    if not mlf.inputs:
        issues.append('WARNING: no inputs defined')
    if not mlf.outputs:
        issues.append('WARNING: no outputs defined')
    for i, v in enumerate(mlf.inputs):
        if not v.get('name'):
            issues.append(f'ERROR: input {i+1} has no name')
    for i, v in enumerate(mlf.outputs):
        if not v.get('name'):
            issues.append(f'ERROR: output {i+1} has no name')
    for i, p in enumerate(mlf.params):
        if not p.get('name'):
            issues.append(f'ERROR: param {i+1} has no name')
    return issues


# ---------------------------------------------------------------------------
# Code generator
# ---------------------------------------------------------------------------

def _build_function_script(mlf: SIRMatlabFunction) -> str:
    """Build the complete MATLAB function text (signature + body + end)."""
    in_names  = [v['name'] for v in mlf.inputs]
    out_names = [v['name'] for v in mlf.outputs]

    if len(out_names) == 0:
        lhs = ''
    elif len(out_names) == 1:
        lhs = f"{out_names[0]} = "
    else:
        lhs = f"[{', '.join(out_names)}] = "

    args = ', '.join(in_names)
    sig = f"function {lhs}{_escape_matlab_str(mlf.name)}({args})"

    # Indent body lines by 4 spaces
    body_lines = mlf.code.rstrip('\n').split('\n')
    body = '\n'.join(('    ' + ln) if ln.strip() else '' for ln in body_lines)

    return f"{sig}\n{body}\nend\n"


def mlf_to_matlab(mlf: SIRMatlabFunction, model_name: str | None = None) -> str:
    """Generate a MATLAB .m script that creates a Simulink model with a MATLAB Function block."""
    if model_name is None:
        model_name = re.sub(r'[^\w]', '_', mlf.name)

    lines: list[str] = []
    lines.append(f'%% Generated by slxgen - MATLAB Function block: {mlf.name}')
    lines.append('')
    lines.append(f"model_name = '{_escape_matlab_str(model_name)}';")
    lines.append("if bdIsLoaded(model_name), close_system(model_name, 0); end")
    lines.append("if exist([model_name '.slx'], 'file'), delete([model_name '.slx']); end")
    lines.append("new_system(model_name);")
    lines.append("load_system(model_name);")
    lines.append('')

    # Add the MATLAB Function block
    lines.append("%% MATLAB Function block")
    lines.append(f"add_block('simulink/User-Defined Functions/MATLAB Function', [model_name '/{_escape_matlab_str(mlf.name)}']);")
    lines.append("rt = sfroot;")
    lines.append("m = rt.find('-isa', 'Stateflow.Machine', 'Name', model_name);")
    lines.append("emc = m.find('-isa', 'Stateflow.EMChart');")
    lines.append(f"emc.Name = '{_escape_matlab_str(mlf.name)}';")

    # Description
    if mlf.desc or mlf.req:
        parts = []
        if mlf.req:
            parts.append(' '.join(f'[{r}]' for r in mlf.req))
        if mlf.desc:
            parts.append(mlf.desc)
        desc_str = ' '.join(parts)
        lines.append(f"emc.Description = {_matlab_str_literal(desc_str)};")

    # Set function script
    func_text = _build_function_script(mlf)
    lines.append('')
    lines.append('%% Function script')
    lines.append(f"emc.Script = {_matlab_str_literal(func_text)};")

    # Set data types on auto-created port objects (find by name after Script is set)
    typed_inputs  = [v for v in mlf.inputs  if v.get('type')]
    typed_outputs = [v for v in mlf.outputs if v.get('type')]

    if typed_inputs or typed_outputs:
        lines.append('')
        lines.append('%% Port data types (set after Script creates ports)')
        for v in typed_inputs + typed_outputs:
            vn = _escape_matlab_str(v['name'])
            lines.append(f"d_ = emc.find('-isa', 'Stateflow.Data', 'Name', '{vn}');")
            lines.append(f"if ~isempty(d_)")
            lines.append(f"    d_.Props.Type.Method = '{_sf_type_method(v['type'])}';")
            lines.append(f"    d_.DataType = '{_escape_matlab_str(v['type'])}';")
            if 'size' in v:
                size_str = ' '.join(str(n) for n in v['size'])
                lines.append(f"    d_.Props.Array.Size = '[{size_str}]';")
            lines.append(f"end")

    # Parameters (workspace variables — not function args, accessed from workspace)
    if mlf.params:
        lines.append('')
        lines.append('%% Parameters (base workspace variables)')
        for p in mlf.params:
            if p.get('value') is not None:
                lines.append(_matlab_param_workspace_assign(p['name'], p.get('type'), p['value']))

    # Finalise
    lines.append('')
    lines.append("Simulink.BlockDiagram.arrangeSystem(model_name);")
    lines.append("save_system(model_name);")
    lines.append(f"disp(['MATLAB Function saved to model: ' model_name]);")

    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------------------
# High-level entry point
# ---------------------------------------------------------------------------

def mlf_yaml_to_matlab(yaml_path, output_path=None, model_name: str | None = None) -> str:
    """Read a YAML file (type: matlab_function) and generate a MATLAB build script.

    Returns the script as a string. If output_path is given, also writes to disk.
    Validation issues are printed to stderr; ERRORs raise ValueError.
    """
    chart_dict = _yaml.safe_load(Path(yaml_path).read_text(encoding='utf-8'))
    mlf = yaml_to_mlf(chart_dict)
    issues = mlf_validate(mlf)
    label = Path(yaml_path).name
    errors = []
    for msg in issues:
        print(f"[MLF:{label}] {msg}", file=sys.stderr)
        if msg.startswith('ERROR'):
            errors.append(msg)
    if errors:
        raise ValueError(f"Validation failed for {yaml_path}:\n" + '\n'.join(errors))

    script = mlf_to_matlab(mlf, model_name=model_name)
    if output_path:
        Path(output_path).write_text(script, encoding='utf-8')
    return script
