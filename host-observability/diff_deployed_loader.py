"""Import shim: diff-deployed.py has a hyphen in its name."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location('diff_deployed', Path(__file__).with_name('diff-deployed.py'))
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
globals().update({k: v for k, v in vars(_module).items() if not k.startswith('__')})
