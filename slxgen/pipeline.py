"""High-level pipeline entry point for slxgen.

Wraps validate -> generate -> (optional) MATLAB run -> (optional) sfLint
into a single call.  The MATLAB Engine is left running after use so the
next call can reconnect to the shared session instead of starting cold.

── Recommended one-time MATLAB setup ──────────────────────────────────────
Open MATLAB, then in the Command Window:
  >> matlab.engine.shareEngine('slxgen')

After that, every run_pipeline(..., run_matlab=True) call connects in <1 s
and you can watch execution in the live MATLAB window.  The session survives
Python restarts.  Use open_desktop=True to get the MATLAB GUI when starting
a fresh engine from Python.
───────────────────────────────────────────────────────────────────────────
"""
from pathlib import Path
import contextlib
import io
import json
import yaml

from .stateflow_sir import yaml_to_sir, sir_validate, sf_yaml_to_sir_json
from .stateflow import sf_yaml_to_matlab as _sf_yaml_to_matlab

_MATLAB_SCRIPTS = Path(__file__).parent / 'matlab'
_SEP = '-' * 60
_SESSION_TIP = "Tip: open MATLAB and run  matlab.engine.shareEngine('{name}')  for a persistent session."


def _hdr(step, total, label):
    print(f'\n[{step}/{total}] {label}')
    print(_SEP)


def run_pipeline(
    yaml_path,
    out_dir=None,
    model_name=None,
    dump_sir=False,
    dump_elk=False,
    run_matlab=False,
    session_name='slxgen',
    open_desktop=False,
    lint=True,
    gen_enums=True,
    gen_sldd=False,
    elk_options=None,
    adaptive_leaf_width=False,
    adaptive_spacing=False,
    default_size=None,
    verbose=True,
):
    """Validate, generate, and optionally build a Stateflow model in MATLAB.

    Parameters
    ----------
    yaml_path : str | Path
        Stateflow YAML source file.
    out_dir : str | Path | None
        Output directory.  Defaults to ``<yaml_path.parent>/generated``.
    model_name : str | None
        Simulink model name.  Defaults to the YAML file stem.
    dump_sir : bool
        Write the SIR to ``<out_dir>/<stem>_sir.json`` after validation.
        Useful for debugging the intermediate representation.
    dump_elk : bool
        Write ELK input/output JSON to ``<out_dir>/elk_input.json`` and
        ``<out_dir>/elk_output.json`` for layout inspection.
    run_matlab : bool
        Connect to (or start) a MATLAB Engine and build the .slx.
    session_name : str
        Name for a newly started shared MATLAB session.
        Match it in MATLAB: ``matlab.engine.shareEngine('slxgen')``.
    open_desktop : bool
        When starting a *new* MATLAB engine, open the full desktop GUI so
        you can inspect the workspace and command window during execution.
        Has no effect when connecting to an existing shared session.
    lint : bool
        Run sfLintChart on the generated .slx (only when run_matlab=True).
    gen_enums : bool
        Generate MATLAB classdef ``.m`` files for any ``enums:`` / ``enum_file:``
        definitions found in the YAML and write them to *out_dir*.  Defaults to
        ``True``; set to ``False`` to skip.
    gen_sldd : bool
        Generate a ``<stem>_sldd.m`` script that creates/updates a Simulink Data
        Dictionary (``.sldd``) containing the enum type definitions.  When
        *run_matlab* is also ``True``, the script is executed before the model
        build so the dictionary is ready before Simulink opens the model.
        Defaults to ``False``.
    elk_options : dict | None
        ELK layout options forwarded to sf_yaml_to_matlab.
    default_size : list | None
        Size used for variables that have no explicit ``size:`` field in YAML.
        ``None`` or ``[1]`` → scalar (Stateflow default, no ``Props.Array.Size``
        emitted). ``[-1]`` → inherited from the connected signal.
    adaptive_leaf_width : bool
        Compute each leaf state's width from its longest label line instead of
        using the fixed ``_SF_LEAF_W`` constant.  Wider states rarely hurt;
        enable by default when chart labels are long.
    adaptive_spacing : bool
        Scale the ELK node gap per compound based on the number of labeled
        transitions between sibling pairs, so stacked labels have room to breathe.
    verbose : bool
        Print step headers and status lines.

    Returns
    -------
    dict
        script  : Path        — generated .m file
        slx     : Path | None — generated .slx (None if run_matlab=False)
        sir     : Path | None — SIR JSON (None if dump_sir=False)
        issues  : list[str]   — SIR validation messages (WARNING/ERROR prefix)
        lint    : list[dict]  — sfLintChart findings (empty when skipped)
        enums   : list[Path]  — enum classdef .m files written (empty if none)
        sldd_script : Path | None — SLDD creation script (None if gen_sldd=False)
        sldd    : Path | None — data dictionary built by MATLAB (None if not run)

    Raises
    ------
    ValueError
        If the YAML has structural ERRORs that prevent generation.
    ImportError
        If run_matlab=True but matlab.engine is not installed.
    """
    yaml_path = Path(yaml_path)
    if out_dir is None:
        out_dir = yaml_path.parent / 'generated'
    out_dir = Path(out_dir)
    out_dir.mkdir(exist_ok=True)
    if model_name is None:
        model_name = yaml_path.stem
    elk_options = dict(elk_options) if elk_options else {}
    if dump_elk:
        elk_options['__dump_elk_dir__'] = str(out_dir)
    if adaptive_leaf_width:
        elk_options['__adaptive_leaf_width__'] = True
    if adaptive_spacing:
        elk_options['__adaptive_spacing__'] = True

    total_steps = 4 if run_matlab else 2

    # ── Step 1: Validate ──────────────────────────────────────────────────────
    if verbose:
        _hdr(1, total_steps, f'Validate   {yaml_path.name}')

    chart_dict = yaml.safe_load(yaml_path.read_text(encoding='utf-8'))
    sir = yaml_to_sir(chart_dict, default_size=default_size)
    validation_issues = sir_validate(sir)

    errors   = [m for m in validation_issues if m.startswith('ERROR')]
    warnings = [m for m in validation_issues if m.startswith('WARNING')]

    if verbose:
        print(f'  States      : {len(sir.states)}')
        print(f'  Transitions : {len(sir.transitions)}')
        print(f'  Variables   : {len(sir.variables)}')
        if errors:
            for msg in errors:
                print(f'  {msg}')
        elif warnings:
            for msg in warnings:
                print(f'  {msg}')
        else:
            print('  Result      : clean')

    if errors:
        raise ValueError('Validation failed:\n' + '\n'.join(errors))

    sir_json_path = None
    if dump_sir:
        sir_json_path = out_dir / (yaml_path.stem + '_sir.json')
        sf_yaml_to_sir_json(yaml_path, output_path=sir_json_path)
        if verbose:
            print(f'  SIR JSON    : {sir_json_path}')

    # ── Step 2: Generate .m script ────────────────────────────────────────────
    script_path = out_dir / (yaml_path.stem + '.m')
    if verbose:
        _hdr(2, total_steps, f'Generate   {script_path.name}')

    with contextlib.redirect_stderr(io.StringIO()):
        _sf_yaml_to_matlab(
            yaml_path,
            export_charts=True,
            output_path=script_path,
            model_name=model_name,
            elk_options=elk_options,
            default_size=default_size,
        )

    if verbose:
        lines = len(script_path.read_text(encoding='utf-8').splitlines())
        print(f'  Written     : {script_path}')
        print(f'  Size        : {lines} lines')

    enum_paths: list[Path] = []
    sldd_script_path: Path | None = None
    if gen_enums or gen_sldd:
        from .enum_gen import load_enums_from_yaml, write_enum_classdefs, enum_sldd_script
        _enums = load_enums_from_yaml(yaml_path)
        if _enums:
            if gen_enums:
                enum_paths = write_enum_classdefs(_enums, out_dir)
                if verbose:
                    for p in enum_paths:
                        print(f'  Enum .m     : {p.name}')
            if gen_sldd:
                sldd_dir = out_dir / 'sldd_gen'
                sldd_dir.mkdir(exist_ok=True)
                sldd_script_path = sldd_dir / (yaml_path.stem + '_sldd.m')
                sldd_script_path.write_text(
                    enum_sldd_script(_enums, yaml_path.stem),
                    encoding='utf-8',
                )
                if verbose:
                    print(f'  SLDD script : sldd_gen/{sldd_script_path.name}')

    result = {
        'script': script_path,
        'slx': None,
        'sir': sir_json_path,
        'issues': validation_issues,
        'lint': [],
        'enums': enum_paths,
        'sldd_script': sldd_script_path,
        'sldd': None,
    }

    if not run_matlab:
        return result

    # ── Step 3: MATLAB — build model ──────────────────────────────────────────
    if verbose:
        _hdr(3, total_steps, 'MATLAB     Build model')

    try:
        import matlab.engine  # type: ignore[import-untyped]
    except ImportError:
        raise ImportError(
            'matlab.engine not available — activate the py311_slxgen env '
            'or call run_pipeline(..., run_matlab=False).'
        )

    sessions = matlab.engine.find_matlab()
    if sessions:
        eng = matlab.engine.connect_matlab(sessions[0])
        if verbose:
            print(f'  Session     : connected to "{sessions[0]}" (existing)')
            print(f'  Note        : session stays open after Python exits')
            print(f'  Tip         : {_SESSION_TIP.format(name=session_name)}')
    else:
        desktop_flag = '-desktop' if open_desktop else ''
        if verbose:
            mode = 'with desktop GUI' if open_desktop else 'headless'
            print(f'  Session     : no shared session found — starting new engine ({mode})...')
        eng = matlab.engine.start_matlab(desktop_flag)
        eng.eval(f"matlab.engine.shareEngine('{session_name}')", nargout=0)
        if verbose:
            print(f'  Session     : started and shared as "{session_name}"')
            print(f'  Note        : session closes when this Python process exits.')
            print()
            print(f'  For a persistent session that survives Python restarts:')
            print(f'    Open MATLAB, then run in the Command Window:')
            print(f'      >> matlab.engine.shareEngine(\'{session_name}\')')

    # Clear MATLAB workspace and close the model if already loaded from a
    # previous run — prevents stale variables and new_system() conflicts.
    if verbose:
        print()
        print(f'  Clearing    : MATLAB workspace + closing "{model_name}" if open')
    eng.eval('clear', nargout=0)
    eng.eval(f"if bdIsLoaded('{model_name}'), bdclose('{model_name}'); end", nargout=0)

    # On Windows the script base name (e.g. hvac_state.m) matches the model
    # name case-insensitively (HVAC_State.slx).  MATLAB's run() refuses to
    # execute when an .slx with the same stem exists in the directory, so
    # delete it here before the script can do so itself.
    _stale_slx = out_dir / (model_name + '.slx')
    if _stale_slx.exists():
        _stale_slx.unlink()

    eng.cd(str(out_dir), nargout=0)

    if gen_sldd and sldd_script_path is not None:
        sldd_dir = sldd_script_path.parent
        if verbose:
            print(f'  Running     : sldd_gen/{sldd_script_path.name}  (create/update .sldd)')
        eng.cd(str(sldd_dir), nargout=0)
        eng.run(str(sldd_script_path), nargout=0)
        sldd_path = sldd_dir / (yaml_path.stem + '.sldd')
        result['sldd'] = sldd_path
        if verbose:
            status = 'OK' if sldd_path.exists() else 'NOT FOUND'
            print(f'  SLDD        : sldd_gen/{sldd_path.name}  [{status}]')
        eng.cd(str(out_dir), nargout=0)

    if verbose:
        print(f'  Running     : {script_path.name}')
    eng.run(str(script_path), nargout=0)

    slx_path = out_dir / (model_name + '.slx')
    result['slx'] = slx_path
    if verbose:
        status = 'OK' if slx_path.exists() else 'NOT FOUND'
        print(f'  Built       : {slx_path.name}  [{status}]')

    # ── Step 4: sfLint (MATLAB) ───────────────────────────────────────────────
    if lint:
        if verbose:
            _hdr(4, total_steps, f'sfLint     {slx_path.name}')

        if not slx_path.exists():
            if verbose:
                print('  Skipped     : .slx not found')
        else:
            lint_json = out_dir / (model_name + '_lint.json')
            eng.addpath(str(_MATLAB_SCRIPTS).replace('\\', '/'), nargout=0)
            eng.slx_lint(  # type: ignore[union-attr]
                str(slx_path).replace('\\', '/'),
                str(lint_json).replace('\\', '/'),
                nargout=0,
            )
            result['lint'] = json.loads(lint_json.read_text(encoding='utf-8'))

            if verbose:
                if result['lint']:
                    print(f'  Issues      : {len(result["lint"])}  ->  {lint_json.name}')
                    for iss in result['lint']:
                        print(f'  [{iss["name"]}] {iss["details"]}')
                else:
                    print(f'  Result      : clean  ->  {lint_json.name}')

    # Engine is intentionally left running so the next call reconnects
    # to the shared session instead of paying the cold-start cost again.

    return result
