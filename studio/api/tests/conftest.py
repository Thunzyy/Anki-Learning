import os
import sys
import tempfile
from pathlib import Path

# Configure isolation during conftest import, before test modules import app.paths.
# A fixture would run too late: paths resolves its globals at import time.
# Keep explicitly configured, nonempty test directories under caller ownership.
_test_data = None
if not os.environ.get("LEARNING_DATA_DIR"):
    _test_data = tempfile.TemporaryDirectory(
        prefix="anki-api-pytest-", ignore_cleanup_errors=True
    )
    os.environ["LEARNING_DATA_DIR"] = _test_data.name

# Fail closed if an external plugin already imported paths with another data root.
_loaded_paths = sys.modules.get("app.paths")
if _loaded_paths is not None and _loaded_paths.DATA_DIR != Path(os.environ["LEARNING_DATA_DIR"]).resolve():
    raise RuntimeError("app.paths was imported before test data isolation; configure LEARNING_DATA_DIR before starting pytest")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
