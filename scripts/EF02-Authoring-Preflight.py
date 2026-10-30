"""Run the optional local authoring preflight without installing the project."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_commerce.remote.authoring_preflight import main


if __name__ == "__main__":
    raise SystemExit(main())
