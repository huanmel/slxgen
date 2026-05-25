"""Generate MATLAB script for Ex1_StMach_sf.yaml."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from slxgen import sf_yaml_to_matlab

YAML   = Path(__file__).parent / 'Ex1_StMach_sf.yaml'
OUTPUT = YAML.with_suffix('.m')

script = sf_yaml_to_matlab(
    YAML,
    output_path=OUTPUT,
    elk_options={'__fault_bus_junctions__': 'true'},
)
print(f'Written: {OUTPUT}')
print(f'Lines:   {len(script.splitlines())}')
