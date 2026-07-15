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


@app.command()
def validate(
    yaml_path: Annotated[Path, typer.Argument(help="Stateflow YAML to validate")],
):
    """Validate a Stateflow YAML and print any issues. Exits 1 on ERRORs."""
    import yaml as _yaml
    from slxgen.stateflow_sir import yaml_to_sir, sir_validate

    chart_dict = _yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    sir = yaml_to_sir(chart_dict)
    issues = sir_validate(sir)
    if issues:
        for msg in issues:
            typer.echo(msg)
    else:
        typer.echo("OK: no issues found")
    has_errors = any(m.startswith("ERROR") for m in issues)
    raise typer.Exit(1 if has_errors else 0)


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


_VALID_OUTPUTS = [
    "report.txt", "arch.md", "slim.json", "full.json",
    "slim.min.json", "sf.yaml", "sf.m",
]


@app.command()
def inspect(
    slx_path: Annotated[Path, typer.Argument(help="Simulink .slx file to inspect")],
    out_dir: Annotated[Optional[Path], typer.Option(
        "--out-dir", "-o", help="Output directory [default: beside the .slx]")] = None,
    outputs: Annotated[Optional[List[str]], typer.Option(
        "--output",
        help=f"Output format to write (repeatable). Choices: {', '.join(_VALID_OUTPUTS)}"
    )] = None,
):
    """Inspect a Simulink .slx and export text/JSON/YAML reports."""
    from slxgen import slx_process

    kwargs: dict = {"filters": {}, "save": True}
    if out_dir is not None:
        kwargs["output_dir"] = str(out_dir)
    if outputs:
        kwargs["outputs"] = outputs
    slx_process(str(slx_path), **kwargs)
    typer.echo("Done.")


def main() -> None:
    app()
