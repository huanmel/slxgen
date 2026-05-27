"""
Compare two Simulink models by converting them to readable JSON/text.

Outputs per model (written next to the .slx file):
  <name>_full.json    - complete parsed model
  <name>_slim.json    - filtered, human/LLM-readable JSON
  <name>_slim.min.json - minified slim JSON (smallest for LLM context)
  <name>_report.txt   - structured text report (best for LLM review)
  <name>_arch.md      - Markdown with hierarchical Mermaid diagrams

Usage:
  python notebooks/compare_models.py
"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
from slxgen import slx_process, compare_models


MODELS = {
    'model1': r'model1.slx',
    'model2': r'model2.slx',
}

FILTERS = {
    'default_attrs': ['name', 'type', 'input_ports', 'output_ports'],
    'default_params': [],
    'block_types': {
        'Constant':                ['Value'],
        'Gain':                    ['Gain'],
        'Sum':                     ['Inputs'],
        'Saturate':                ['UpperLimit', 'LowerLimit'],
        'Switch':                  ['Criteria', 'Threshold'],
        'Lookup_n-D':              ['Table', 'BreakpointsForDimension1', 'BreakpointsForDimension2'],
        'PreLookup':               ['BreakpointsData'],
        'Interpolation_n-D':       ['Table'],
        'RateLimiter':             ['RisingSlew', 'FallingSlew'],
        'DiscreteIntegrator':      ['gainval', 'InitialCondition'],
        'TransferFcn':             ['Numerator', 'Denominator'],
        'PID Controller':          ['P', 'I', 'D', 'N', 'Form', 'TimeDomain'],
        'Discrete PID Controller': ['P', 'I', 'D', 'N', 'Form', 'TimeDomain'],
        'From':                    ['GotoTag'],
        'Goto':                    ['GotoTag'],
        'EnablePort':              ['StatesWhenEnabling'],
    },
    'skip_blocks': [
        'SignalConversion',
        'DataTypeConversion',
        'Terminator',
    ],
}


# if __name__ == '__main__':
# %%
results = {}
for name, path in MODELS.items():
    print(f"\n{'='*60}\nProcessing: {name}\n  {path}")
    slim = slx_process(path, FILTERS, save=True)
    results[name] = slim
    arch_path = Path(path).with_suffix('').as_posix() + '_arch.md'
    print(f"  blocks: {len(slim['blocks'])}  connections: {len(slim.get('connection_info', []))}")
    print(f"  arch diagram: {arch_path}")
    print("\n--- Signal flow (root) ---")
    for line in slim.get('connection_info', []):
        print(f"  {line}")

compare_models(results)
# %%

