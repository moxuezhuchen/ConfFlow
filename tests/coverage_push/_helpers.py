import builtins
import importlib
from unittest.mock import patch


def reload_with_import_block(module, blocked_top_level_name: str):
    """Reload `module` while making `import blocked_top_level_name` raise ImportError."""

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == blocked_top_level_name:
            raise ImportError(f"blocked: {blocked_top_level_name}")
        return real_import(name, globals, locals, fromlist, level)

    with patch("builtins.__import__", side_effect=fake_import):
        return importlib.reload(module)
