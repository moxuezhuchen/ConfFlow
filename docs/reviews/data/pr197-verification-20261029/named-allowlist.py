"""Run the same bare-name allowlist workflow against either source tree."""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("case_helper", sys.argv[1])
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)
root = Path(tempfile.mkdtemp(prefix="confflow-named-allowlist."))
with pytest.MonkeyPatch.context() as mp:
    paths = cg.two_xtb.__wrapped__(root, mp)
    document = cg._document(paths, allowed=Path("xtb"))
    try:
        result = cg._run(document, root / "run", cg.toy_text.__wrapped__())
        print(json.dumps({"status": result.status, "launches": len(cg._launches(paths["record"]))}))
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
