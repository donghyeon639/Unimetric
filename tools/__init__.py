"""Developer tools; excluded from the application distribution."""

from pathlib import Path
import sys

source_path = str(Path(__file__).resolve().parents[1] / "src")
if source_path not in sys.path:
    sys.path.insert(0, source_path)
