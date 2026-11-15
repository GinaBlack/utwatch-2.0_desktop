import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("UTWATCH_PBKDF2_ITERATIONS", "1000")      # fast hashing in tests
os.environ["UTWATCH_HOME"] = tempfile.mkdtemp(prefix="utwatch_test_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
