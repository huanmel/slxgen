"""Command-line interface for slxgen."""
import typer
from pathlib import Path
from typing import Optional, List
from typing import Annotated

app = typer.Typer(
    name="slxgen",
    help="Generate and inspect MATLAB/Simulink Stateflow charts.",
    no_args_is_help=True,
)

# Path to the built-in default filter config shipped with the package
_DEFAULT_FILTERS_YML = Path(__file__).parent.parent / "data" / "slx_filters_default.yml"


def _load_filters(config: Optional[Path], no_filter: bool) -> dict:
    """Load filter dict from config YAML, default config, or return empty."""
    import yaml as _yaml
    if no_filter:
        return {}
    path = config if config is not None else _DEFAULT_FILTERS_YML
    if path.exists():
        return _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if config is not None:
        typer.echo(f"ERROR: config file not found: {config}", err=True)
        raise typer.Exit(1)
    return {}  # default file missing (editable install edge case)


# ── generate ──────────────────────────────────────────────────────────────────

@app.command()
def generate(
    yaml_path: Annotated[Path, typer.Argument(help="Stateflow YAML source file")],
    out_dir: Annotated[Optional[Path], typer.Option(
        "--out-dir", "-o", help="Output directory [default: <yaml_dir>/generated]")] = None,
    model_name: Annotated[Optional[str], typer.Option(
        "--model-name", "-m", help="Simulink model name [default: YAML stem]")] = None,
    run_matlab: Annotated[bool, typer.Option(
        "--run-matlab/--no-matlab", help="Connect to MATLAB and build .slx")] = False,
    session: Annotated[str, typer.Option(
        help="MATLAB shared session name")] = "slxgen",
    subsys_ref: Annotated[bool, typer.Option(
        "--subsys-ref/--no-subsys-ref",
        help="Also export a Subsystem Reference .slx component")] = False,
    lint: Annotated[bool, typer.Option(
        "--lint/--no-lint",
        help="Run sfLintChart after build (requires --run-matlab)")] = True,
    gen_enums: Annotated[bool, typer.Option(
        "--enums/--no-enums", help="Write enum classdef .m files")] = True,
    dump_sir: Annotated[bool, typer.Option(
        "--dump-sir", help="Write <stem>_sir.json intermediate representation")] = False,
):
    """Generate a Stateflow model from a YAML specification."""
    from slxgen import run_pipeline
    try:
        result = run_pipeline(
            yaml_path,
            out_dir=out_dir,
            model_name=model_name,
            run_matlab=run_matlab,
            session_name=session,
            subsys_ref=subsys_ref,
            lint=lint,
            gen_enums=gen_enums,
            dump_sir=dump_sir,
            verbose=True,
        )
    except ValueError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)
    except ImportError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(1)

    has_errors = any(m.startswith("ERROR") for m in result["issues"])
    raise typer.Exit(1 if has_errors else 0)


# ── validate ──────────────────────────────────────────────────────────────────

@app.command()
def validate(
    yaml_path: Annotated[Path, typer.Argument(help="Stateflow YAML to validate")],
):
    """Validate a YAML specification and print any issues. Exits 1 on ERRORs."""
    import yaml as _yaml

    chart_dict = _yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    yaml_type = chart_dict.get("type", "stateflow")

    if yaml_type == "matlab_function":
        from slxgen.matlab_function import yaml_to_mlf, mlf_validate
        mlf = yaml_to_mlf(chart_dict)
        issues = mlf_validate(mlf)
    else:
        from slxgen.stateflow_sir import yaml_to_sir, sir_validate
        sir = yaml_to_sir(chart_dict)
        issues = sir_validate(sir)

    if issues:
        for msg in issues:
            typer.echo(msg)
    else:
        typer.echo("OK: no issues found")
    has_errors = any(m.startswith("ERROR") for m in issues)
    raise typer.Exit(1 if has_errors else 0)


# ── puml ──────────────────────────────────────────────────────────────────────

@app.command()
def puml(
    yaml_path: Annotated[Path, typer.Argument(help="Stateflow YAML source file")],
    output: Annotated[Optional[Path], typer.Option(
        "--output", "-o", help="Write to file [default: print to stdout]")] = None,
):
    """Export a Stateflow YAML to PlantUML (@startuml)."""
    from slxgen import sf_yaml_to_puml

    text = sf_yaml_to_puml(yaml_path, output_path=output)
    if output is None:
        typer.echo(text)
    else:
        typer.echo(f"Written: {output}")


# ── inspect ───────────────────────────────────────────────────────────────────

_VALID_OUTPUTS = [
    "report.txt", "arch.md", "slim.json", "full.json",
    "slim.min.json", "sf.yaml", "sf.m",
]


@app.command()
def inspect(
    slx_path: Annotated[Path, typer.Argument(help="Simulink .slx file to inspect")],
    out_dir: Annotated[Optional[Path], typer.Option(
        "--out-dir", "-o", help="Output directory [default: <stem>_reports/ beside .slx]")] = None,
    outputs: Annotated[Optional[List[str]], typer.Option(
        "--output",
        help=f"Output format (repeatable). Choices: {', '.join(_VALID_OUTPUTS)}"
    )] = None,
    # Filter config
    config: Annotated[Optional[Path], typer.Option(
        "--config", "-c",
        help="Filter config YAML [default: built-in slx_filters_default.yml]")] = None,
    no_filter: Annotated[bool, typer.Option(
        "--no-filter", help="Skip all filtering (raw output)")] = False,
    # Tree traversal
    proj_root: Annotated[Optional[Path], typer.Option(
        "--proj-root",
        help="Project root for resolving referenced sub-models. "
             "When set, processes the full model tree instead of a single .slx."
    )] = None,
    parse_libraries: Annotated[bool, typer.Option(
        "--parse-libraries/--no-parse-libraries",
        help="Also parse referenced Simulink libraries (SourceBlock refs). "
             "Only used with --proj-root."
    )] = False,
):
    """Inspect a Simulink .slx and export text/JSON/YAML reports.

    By default uses the built-in filter config (slx_filters_default.yml).
    Pass --config to override or --no-filter for raw unfiltered output.

    With --proj-root, resolves and processes referenced sub-models recursively
    (equivalent to process_model_tree).
    """
    from slxgen import slx_process, process_model_tree

    filters = _load_filters(config, no_filter)
    out_dir_str = str(out_dir) if out_dir else None
    out_list = outputs or None

    if proj_root is not None:
        tree_kwargs: dict = {"save": True, "parse_libraries": parse_libraries}
        if out_dir_str:
            tree_kwargs["output_dir"] = out_dir_str
        if out_list:
            tree_kwargs["outputs"] = out_list
        results = process_model_tree(str(slx_path), filters, str(proj_root), **tree_kwargs)
        typer.echo(f"\nDone. {len(results)} model(s) processed.")
    else:
        kwargs: dict = {"filters": filters, "save": True}
        if out_dir_str:
            kwargs["output_dir"] = out_dir_str
        if out_list:
            kwargs["outputs"] = out_list
        slx_process(str(slx_path), **kwargs)
        typer.echo("Done.")


# ── config ────────────────────────────────────────────────────────────────────

@app.command()
def config(
    action: Annotated[str, typer.Argument(
        help="Action: 'show' prints the default config, 'init' writes it to a file"
    )] = "show",
    output: Annotated[Optional[Path], typer.Option(
        "--output", "-o",
        help="Destination for 'init' [default: ./slx_filters.yml]")] = None,
):
    """Manage the filter configuration file.

    \b
    slxgen config show              Print the built-in default config to stdout
    slxgen config init              Copy default config to ./slx_filters.yml
    slxgen config init -o my.yml   Copy default config to a custom path
    """
    if action == "show":
        if _DEFAULT_FILTERS_YML.exists():
            typer.echo(_DEFAULT_FILTERS_YML.read_text(encoding="utf-8"))
        else:
            typer.echo(f"Default config not found at: {_DEFAULT_FILTERS_YML}", err=True)
            raise typer.Exit(1)

    elif action == "init":
        dest = output or Path("slx_filters.yml")
        if not _DEFAULT_FILTERS_YML.exists():
            typer.echo(f"ERROR: default config not found at: {_DEFAULT_FILTERS_YML}", err=True)
            raise typer.Exit(1)
        if dest.exists():
            typer.echo(f"File already exists: {dest}  (remove it first or use -o to specify another path)")
            raise typer.Exit(1)
        dest.write_text(_DEFAULT_FILTERS_YML.read_text(encoding="utf-8"), encoding="utf-8")
        typer.echo(f"Written: {dest}")
        typer.echo("Edit it, then pass it with:  slxgen inspect model.slx --config slx_filters.yml")

    else:
        typer.echo(f"ERROR: unknown action '{action}'. Use 'show' or 'init'.", err=True)
        raise typer.Exit(1)


def main() -> None:
    app()
