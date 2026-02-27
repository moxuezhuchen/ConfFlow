#!/usr/bin/env python3

"""ConfFlow unified logging system.

Provides the ConfFlowLogger singleton and global log redirection utilities.
"""

from __future__ import annotations

import logging
import sys

__all__ = [
    "ConfFlowLogger",
    "get_logger",
    "redirect_logging_streams",
]


class ConfFlowLogger:
    """ConfFlow unified log manager.

    Supports two run modes:

    1. Standalone: full console + file logging.
    2. Embedded (called by GibbsFlow, etc.): only uses the parent process
       logging system.
    """

    _instance = None
    _initialized = False
    _embedded_mode = False  # Whether running in embedded mode (e.g. called by GibbsFlow)

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if ConfFlowLogger._initialized:
            return
        ConfFlowLogger._initialized = True

        self.logger = logging.getLogger("confflow")
        self.logger.setLevel(logging.DEBUG)
        self.handlers: dict[str, logging.Handler] = {}

        # Check whether called in embedded mode (parent logger already configured)
        root_logger = logging.getLogger()
        if root_logger.hasHandlers():
            # Parent process already configured logging; use embedded mode
            ConfFlowLogger._embedded_mode = True
            # Propagate to the parent logger
            self.logger.propagate = True
        else:
            # Standalone run; add our own handlers
            self.logger.propagate = False
            self._add_console_handler()

    @classmethod
    def set_embedded_mode(cls, enabled: bool = True):
        """Set embedded mode (called by external callers such as GibbsFlow)."""
        cls._embedded_mode = enabled
        if cls._instance:
            cls._instance.logger.propagate = enabled
            # If embedded mode is enabled, remove the standalone console handler
            if enabled and "console" in cls._instance.handlers:
                cls._instance.logger.removeHandler(cls._instance.handlers["console"])
                del cls._instance.handlers["console"]

    def _add_console_handler(self):
        """Add a console log handler."""
        if "console" in self.handlers:
            return

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)

        # Compact formatter: time + level + message
        formatter = logging.Formatter(
            "[%(asctime)s]  %(levelname)-5s  %(message)s", datefmt="%H:%M:%S"
        )
        console_handler.setFormatter(formatter)

        self.logger.addHandler(console_handler)
        self.handlers["console"] = console_handler

    def redirect_console_handler(self, stream=None) -> None:
        """Redirect the console handler output stream to *stream* (default: current ``sys.stdout``)."""
        if stream is None:
            stream = sys.stdout

        handler = self.handlers.get("console")
        if handler is None:
            return

        if isinstance(handler, logging.StreamHandler):
            try:
                handler.setStream(stream)
            except AttributeError:
                try:
                    handler.stream = stream  # type: ignore[attr-defined]
                except AttributeError:
                    pass

    def add_file_handler(self, log_file: str, level: int = logging.DEBUG):
        """Add a file log handler.

        Skipped in embedded mode (uses the parent process log file instead).
        """
        # Skip in embedded mode
        if ConfFlowLogger._embedded_mode:
            return

        if "file" in self.handlers:
            self.logger.removeHandler(self.handlers["file"])

        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(level)

        formatter = logging.Formatter(
            "%(asctime)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(formatter)

        self.logger.addHandler(file_handler)
        self.handlers["file"] = file_handler

    def set_level(self, level: int):
        """Set the logging level."""
        self.logger.setLevel(level)
        for handler in self.handlers.values():
            handler.setLevel(level)

    def close(self):
        """Close all handlers."""
        for handler in list(self.handlers.values()):
            handler.close()
            self.logger.removeHandler(handler)
        self.handlers.clear()

    # Convenience methods
    def debug(self, msg, *args, **kwargs):
        self.logger.debug(msg, *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        self.logger.info(msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        self.logger.warning(msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        self.logger.error(msg, *args, **kwargs)

    def critical(self, msg, *args, **kwargs):
        self.logger.critical(msg, *args, **kwargs)

    def exception(self, msg, *args, **kwargs):
        self.logger.exception(msg, *args, **kwargs)


def get_logger() -> ConfFlowLogger:
    """Return the global ConfFlowLogger singleton."""
    return ConfFlowLogger()


def redirect_logging_streams(stream=None, include_root: bool = False) -> None:
    """Redirect all existing StreamHandler output streams to *stream*."""
    if stream is None:
        stream = sys.stdout

    targets = [logging.getLogger("confflow")]
    if include_root:
        targets.insert(0, logging.getLogger())

    for lg in targets:
        for handler in list(getattr(lg, "handlers", [])):
            if isinstance(handler, logging.StreamHandler):
                try:
                    handler.setStream(stream)
                except AttributeError:
                    try:
                        handler.stream = stream  # type: ignore[attr-defined]
                    except AttributeError:
                        pass
