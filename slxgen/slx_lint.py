"""
slx_lint — Python wrapper for the MATLAB sfLintChart linter.

Runs sfLintChart on every Stateflow chart inside an .slx file by calling
MATLAB in -batch mode, reads the JSON result, and returns issue strings in
the same WARNING:/ERROR: format as sir_validate().

Requires MATLAB on the system PATH.
"""

import json
import subprocess
import tempfile
from pathlib import Path

_MATLAB_DIR = Path(__file__).parent / 'matlab'


def slx_lint(slx_path: str | Path) -> list[str]:
    """Run sfLintChart on every Stateflow chart in *slx_path* via MATLAB.

    Returns list[str] of WARNING:-prefixed issue strings.
    Returns a single ERROR: string if MATLAB fails or produces no output.
    """
    slx_path = Path(slx_path).resolve()
    tmp_json = Path(tempfile.mktemp(suffix='.json'))

    matlab_dir = str(_MATLAB_DIR).replace('\\', '/')
    slx_str    = str(slx_path).replace('\\', '/')
    json_str   = str(tmp_json).replace('\\', '/')

    batch = f"addpath('{matlab_dir}'); slx_lint('{slx_str}', '{json_str}')"

    try:
        result = subprocess.run(
            ['matlab', '-batch', batch],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or '').strip()
        return [f"ERROR: slx_lint MATLAB call failed: {stderr}"]
    except FileNotFoundError:
        return ['ERROR: slx_lint requires MATLAB on PATH — matlab executable not found']
    except subprocess.TimeoutExpired:
        return ['ERROR: slx_lint timed out (>120 s)']

    if not tmp_json.exists():
        stdout = (result.stdout or '').strip()
        return [f"ERROR: slx_lint produced no JSON output. MATLAB stdout: {stdout}"]

    try:
        with open(tmp_json, encoding='utf-8') as f:
            issues = json.load(f)
    except json.JSONDecodeError as e:
        return [f"ERROR: slx_lint JSON parse failed: {e}"]
    finally:
        tmp_json.unlink(missing_ok=True)

    return [
        f"WARNING: [sfLint:{iss['chart']}] {iss['name']}: {iss['details']}"
        for iss in issues
    ]


def print_lint_report(slx_path: str | Path) -> int:
    """Run slx_lint and print a formatted report. Returns issue count."""
    issues = slx_lint(slx_path)
    if not issues:
        print(f"sfLint {Path(slx_path).name}: clean")
        return 0
    print(f"sfLint {Path(slx_path).name}: {len(issues)} issue(s)")
    for i, msg in enumerate(issues, 1):
        print(f"  [{i}] {msg}")
    return len(issues)
