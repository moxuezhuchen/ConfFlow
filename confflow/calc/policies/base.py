#!/usr/bin/env python3

"""Calculation Policy Abstract Base Class."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any

__all__ = [
    "CalculationPolicy",
]


class CalculationPolicy(ABC):
    """Abstract base class for calculation software policies."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the name of the calculation software (e.g., 'gaussian', 'orca')."""
        pass

    @property
    @abstractmethod
    def input_ext(self) -> str:
        """Return the input file extension (e.g., 'gjf', 'inp')."""
        pass

    @property
    @abstractmethod
    def log_ext(self) -> str:
        """Return the log/output file extension (e.g., 'log', 'out')."""
        pass

    @abstractmethod
    def generate_input(self, task_info: dict[str, Any], inp_file_path: str) -> None:
        """Generate the input file for the calculation."""
        pass

    @abstractmethod
    def parse_output(
        self, log_file: str, config: dict[str, Any], is_sp_task: bool = False
    ) -> dict[str, Any]:
        """Parse the output file to extract results."""
        pass

    @abstractmethod
    def get_execution_command(self, config: dict[str, Any], inp_file: str) -> list[str]:
        """Construct the command line arguments to execute the calculation."""
        pass

    def get_environment(self, config: dict[str, Any], cmd: list[str]) -> dict[str, str]:
        """Get the environment variables for the execution."""
        return os.environ.copy()

    @abstractmethod
    def check_termination(self, log_file: str) -> bool:
        """Check if the calculation terminated normally."""
        pass

    @abstractmethod
    def get_error_details(self, work_dir: str, job_name: str, config: dict[str, Any]) -> str:
        """Extract error details from the log file."""
        pass

    @abstractmethod
    def cleanup_lingering_processes(self, config: dict[str, Any]) -> None:
        """Kill any lingering processes associated with this software."""
        pass
