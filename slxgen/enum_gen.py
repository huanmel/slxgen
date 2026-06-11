"""Generate MATLAB enum classdef files from YAML specifications.

YAML schema — inline in a model YAML file or in a standalone enum YAML:

    enums:
      FanMode_e:
        storage: int8          # optional; omit for MATLAB default (int32)
        default: STANDBY       # optional; first member used if omitted
        members:
          STANDBY: 0
          BOOST:   1
          AUTO:    2
          MANUAL:  3

Model YAML may also link a shared data file (enums, and in future bus objects):

    data_file: ../shared/project_types.yaml   # relative to the model YAML

If both ``data_file`` and inline ``enums`` are present, the inline
definitions take precedence (override) over the linked file.

Generated file ``FanMode_e.m``:

    classdef FanMode_e < Simulink.IntEnumType
      enumeration
        STANDBY (0)
        BOOST   (1)
        AUTO    (2)
        MANUAL  (3)
      end
      methods (Static)
        function retVal = getDefaultValue()
          retVal = FanMode_e.STANDBY;
        end
        function retVal = getStorageType()
          retVal = 'int8';
        end
      end
    end

The ``getStorageType`` method is only emitted when ``storage`` is specified.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def enum_classdef(name: str, spec: dict[str, Any]) -> str:
    """Return a MATLAB classdef string for one enumeration.

    Parameters
    ----------
    name:
        Enum type name, e.g. ``FanMode_e``.
    spec:
        Dict with keys:

        ``members`` (required)
            ``{member_name: int_value, ...}`` — insertion order preserved.
        ``storage`` (optional)
            MATLAB storage type string, e.g. ``'int8'``.
        ``default`` (optional)
            Default member name; first member used if omitted.
    """
    members: dict[str, int] = spec['members']
    storage: str | None = spec.get('storage')
    default_key: str = spec.get('default', next(iter(members)))

    # PyYAML parses bare OFF/ON/YES/NO as booleans; normalise to strings.
    members = {str(k): v for k, v in members.items()}
    default_key = str(default_key)

    max_name_len = max(len(k) for k in members)
    lines = [
        f'classdef {name} < Simulink.IntEnumType',
        '  enumeration',
    ]
    for member_name, value in members.items():
        pad = ' ' * (max_name_len - len(member_name))
        lines.append(f'    {member_name}{pad} ({value})')
    lines += [
        '  end',
        '  methods (Static)',
        '    function retVal = getDefaultValue()',
        f'      retVal = {name}.{default_key};',
        '    end',
    ]
    if storage:
        lines += [
            '    function retVal = getStorageType()',
            f"      retVal = '{storage}';",
            '    end',
        ]
    lines += [
        '  end',
        'end',
        '',
    ]
    return '\n'.join(lines)


def load_enums_from_yaml(yaml_path: str | Path) -> dict[str, dict]:
    """Load enum definitions referenced by a model YAML file.

    Checks for ``data_file`` (linked file loaded first) and then for an
    inline ``enums`` key (overrides the linked file).  Returns ``{}`` when
    neither key is present.
    """
    import yaml as _yaml

    yaml_path = Path(yaml_path)
    chart: dict = _yaml.safe_load(yaml_path.read_text(encoding='utf-8'))

    result: dict[str, dict] = {}

    enum_file = chart.get('data_file')
    if enum_file:
        linked_path = yaml_path.parent / enum_file
        linked: dict = _yaml.safe_load(linked_path.read_text(encoding='utf-8'))
        result.update(linked.get('enums', {}))

    result.update(chart.get('enums', {}))
    return result


def write_enum_classdefs(enums: dict[str, dict],
                         output_dir: str | Path) -> list[Path]:
    """Write one ``<Name>.m`` classdef file per enum into *output_dir*.

    Creates *output_dir* if it does not exist.  Returns the list of paths
    written.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, spec in enums.items():
        text = enum_classdef(name, spec)
        path = output_dir / f'{name}.m'
        path.write_text(text, encoding='utf-8')
        written.append(path)
    return written


def sf_yaml_to_enum_classdefs(yaml_path: str | Path,
                               output_dir: str | Path | None = None,
                               ) -> dict[str, str]:
    """Generate MATLAB classdef strings for all enums in a model YAML.

    Loads enum definitions (inline and/or linked file), generates one
    classdef string per type, and optionally writes ``<Name>.m`` files to
    *output_dir*.

    Returns a ``{type_name: classdef_text}`` dict.
    """
    enums = load_enums_from_yaml(yaml_path)
    result = {name: enum_classdef(name, spec) for name, spec in enums.items()}
    if output_dir is not None:
        write_enum_classdefs(enums, output_dir)
    return result


def enum_sldd_script(enums: dict[str, dict], sldd_name: str) -> str:
    """Return a MATLAB script that creates or updates ``<sldd_name>.sldd``.

    The script uses ``Simulink.defineIntEnumType`` to define each type in the
    base workspace, then calls ``importEnumTypes`` to pull the definitions into
    the data dictionary.  Running it a second time re-opens the existing
    ``.sldd`` and re-imports, so it is safe to re-run after YAML changes.

    Parameters
    ----------
    enums:
        ``{type_name: spec}`` as returned by ``load_enums_from_yaml``.
    sldd_name:
        Base name for the ``.sldd`` file (no extension), e.g. ``'fan_ctrl_sf'``.
    """
    lines = [
        f'% Generated by slxgen - initialise {sldd_name}.sldd',
        '% Run in MATLAB to create / update the Simulink Data Dictionary.',
        f"sldd_path = fullfile(fileparts(mfilename('fullpath')), '{sldd_name}.sldd');",
        '',
        '% Define enum types in the base workspace',
    ]

    type_names = list(enums.keys())

    for name, spec in enums.items():
        members: dict[str, int] = {str(k): v for k, v in spec['members'].items()}
        storage: str | None = spec.get('storage')
        default_key: str = str(spec.get('default', next(iter(members))))

        names_str = '{' + ', '.join(f"'{m}'" for m in members) + '}'
        vals_str  = '[' + ' '.join(str(v) for v in members.values()) + ']'

        # Simulink.defineIntEnumType(Name, CellOfNames, IntValues, ...)
        block = [f"Simulink.defineIntEnumType('{name}', {names_str}, {vals_str}, ..."]
        block.append(f"    'DefaultValue', '{default_key}', ...")
        block.append( "    'AddClassNameToEnumNames', true")
        if storage:
            block[-1] += ', ...'
            block.append(f"    'StorageType', '{storage}'")
        block[-1] += ');'

        lines.extend(block)
        lines.append('')

    type_list = ', '.join(f"'{n}'" for n in type_names)
    lines += [
        '% Create or open the data dictionary',
        "if exist(sldd_path, 'file')",
        "    dict = Simulink.data.dictionary.open(sldd_path);",
        "else",
        "    dict = Simulink.data.dictionary.create(sldd_path);",
        "end",
        '',
        f'importEnumTypes(dict, {{{type_list}}});',
        'saveChanges(dict);',
        'clear dict;',
        "disp(['Saved: ' sldd_path]);",
    ]

    return '\n'.join(lines)


def sf_yaml_to_sldd_script(yaml_path: str | Path,
                            output_path: str | Path | None = None,
                            sldd_name: str | None = None) -> str:
    """Generate a MATLAB script that creates/updates an ``.sldd`` for a model YAML.

    The ``.sldd`` name defaults to the YAML file stem.  If *output_path* is
    given the script is written to that file; the script text is returned
    regardless.
    """
    import yaml as _yaml
    yaml_path = Path(yaml_path)
    if sldd_name is None:
        _chart = _yaml.safe_load(yaml_path.read_text(encoding='utf-8'))
        _data_file = _chart.get('data_file')
        sldd_name = Path(_data_file).stem if _data_file else yaml_path.stem
    enums = load_enums_from_yaml(yaml_path)
    text = enum_sldd_script(enums, sldd_name)
    if output_path is not None:
        Path(output_path).write_text(text, encoding='utf-8')
    return text
