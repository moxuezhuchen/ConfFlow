"""Negative fixture (raw_text rule): the retired token hides in a docstring.

The V1 wire protocol lives at confflow.workflow.v1 and must be caught by the
raw-text rule AP-034 even though it is only prose here.
"""


def documented() -> str:
    return "ok"
