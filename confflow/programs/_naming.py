#!/usr/bin/env python3

"""Shared program-adapter naming helpers.

The Gaussian and ORCA renderers derive filesystem-safe job names from
workflow logical keys with exactly the same algorithm; this module is the
single authority so the two program renderers cannot drift apart.  It is
private to the ``confflow.programs`` package and imports the standard
library only.
"""

from __future__ import annotations

import re

__all__ = ["sanitize_job_name"]

#: Characters outside the portable ``[A-Za-z0-9_.-]`` filename vocabulary
#: collapse to ``_``.
_JOB_SANITIZE_PATTERN = re.compile(r"[^A-Za-z0-9_.\-]+")

#: Longest job name both program filesystems accept.
_MAX_JOB_LENGTH: int = 128


def sanitize_job_name(value: str, *, fallback: str = "job") -> str:
    """Sanitize a logical key into a filesystem-safe program job name.

    Parameters
    ----------
    value : str
        Candidate job name, usually the work-item logical key.
    fallback : str, optional
        Name used when nothing sanitizable remains.

    Returns
    -------
    str
        Deterministic job name containing only ``[A-Za-z0-9_.-]``.
    """
    cleaned = _JOB_SANITIZE_PATTERN.sub("_", str(value).strip()).strip("._")
    if not cleaned:
        cleaned = _JOB_SANITIZE_PATTERN.sub("_", str(fallback).strip()).strip("._")
    if not cleaned:
        cleaned = "job"
    return cleaned[:_MAX_JOB_LENGTH]
