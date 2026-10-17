"""Make the checkout's application and developer tools importable."""
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
for path in (root, root / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
