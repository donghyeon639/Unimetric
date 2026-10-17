"""Run the src-layout checkout without requiring an editable install."""
from pathlib import Path
import sys


source_path = str(Path(__file__).resolve().parent / "src")
if source_path not in sys.path:
    sys.path.insert(0, source_path)


def main() -> int:
    from unimetric.cli import main as cli_main
    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
