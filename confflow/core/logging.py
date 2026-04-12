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
    _embedded_mode = False  # Track whether logging is delegated to a parent process.
    _embedded_mode_override: bool | None = None

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

        self._apply_embedded_mode(self._resolve_embedded_mode())

    @classmethod
    def _resolve_embedded_mode(cls) -> bool:
        """Resolve embedded mode from an explicit override or host logger state."""
        if cls._embedded_mode_override is not None:
            return cls._embedded_mode_override
        return cls._should_auto_embed()

    @classmethod
    def _should_auto_embed(cls) -> bool:
        """Auto-enable embedded mode for real host loggers, not pytest capture handlers."""
        root_logger = logging.getLogger()
        root_handlers = list(getattr(root_logger, "handlers", []))
        if not root_handlers:
            return False

        return any(not cls._is_pytest_handler(handler) for handler in root_handlers)

    @staticmethod
    def _is_pytest_handler(handler: logging.Handler) -> bool:
        """Return True for pytest's internal logging handlers."""
        return type(handler).__module__.startswith("_pytest.")

    @classmethod
    def set_embedded_mode(cls, enabled: bool = True):
        """Set embedded mode (called by external callers such as GibbsFlow)."""
        cls._embedded_mode_override = True if enabled else None
        cls._embedded_mode = enabled
        if cls._instance:
            cls._instance._apply_embedded_mode(enabled)

    def _apply_embedded_mode(self, enabled: bool) -> None:
        """Apply the resolved run mode to the logger instance."""
        ConfFlowLogger._embedded_mode = enabled
        self.logger.propagate = enabled
        if enabled:
            self._remove_console_handler()
        else:
            self._add_console_handler()

    def _remove_console_handler(self) -> None:
        """Remove the standalone console handler if present."""
        handler = self.handlers.pop("console", None)
        if handler is None:
            return
        self.logger.removeHandler(handler)

    def _add_console_handler(self):
        """Add a console log handler."""
        if "console" in self.handlers:
            return

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)

        # Keep console output compact and scan-friendly.
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
