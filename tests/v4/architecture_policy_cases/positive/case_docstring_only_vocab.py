"""Positive fixture: banned words appear only inside this docstring.

TaskRunner, output_path, result.xyz and input_xyz are mentioned as prose;
the AST-based vocabulary scans must not flag them.
"""


def documented() -> str:
    return "ok"
