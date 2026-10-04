"""Acceptor tool: disable the venv's editable-install finder.

The ConfFlow venv maps ``confflow`` to the /opt/ConfFlow checkout through an
editable finder.  When a worktree deletes a module the finder still finds it in
/opt/ConfFlow, so tests that import a deleted module pass falsely.  Put this
directory first on PYTHONPATH (together with the worktree root) to disable it.
"""

import sys


def _is_editable(obj: object) -> bool:
    return "__editable__" in str(getattr(obj, "__module__", ""))


sys.meta_path[:] = [finder for finder in sys.meta_path if not _is_editable(finder)]
sys.path_hooks[:] = [hook for hook in sys.path_hooks if not _is_editable(hook)]
sys.path_importer_cache.clear()
