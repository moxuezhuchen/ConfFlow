#!/usr/bin/env python3
"""Hotspot tests for core.logging."""

from __future__ import annotations

import io
import logging
from unittest.mock import MagicMock, patch

import confflow.core.logging as cf_logging


def _reset_logger_singleton() -> None:
    logger = logging.getLogger("confflow")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    cf_logging.ConfFlowLogger._instance = None
    cf_logging.ConfFlowLogger._initialized = False
    cf_logging.ConfFlowLogger._embedded_mode = False


def test_confflow_logger_standalone_adds_console_handler():
    _reset_logger_singleton()
    root_mock = MagicMock()
    root_mock.hasHandlers.return_value = False
    real_get_logger = logging.getLogger

    def fake_get_logger(name=None):
        if name is None:
            return root_mock
        return real_get_logger(name)

    with patch("logging.getLogger", side_effect=fake_get_logger):
        logger = cf_logging.ConfFlowLogger()

    assert logger.logger.propagate is False
    assert "console" in logger.handlers
    logger.close()


def test_confflow_logger_embedded_mode_skips_console_handler():
    _reset_logger_singleton()
    root_mock = MagicMock()
    root_mock.hasHandlers.return_value = True
    real_get_logger = logging.getLogger

    def fake_get_logger(name=None):
        if name is None:
            return root_mock
        return real_get_logger(name)

    with patch("logging.getLogger", side_effect=fake_get_logger):
        logger = cf_logging.ConfFlowLogger()

    assert logger.logger.propagate is True
    assert "console" not in logger.handlers


def test_set_embedded_mode_removes_console_handler():
    _reset_logger_singleton()
    root_mock = MagicMock()
    root_mock.hasHandlers.return_value = False
    real_get_logger = logging.getLogger

    def fake_get_logger(name=None):
        if name is None:
            return root_mock
        return real_get_logger(name)

    with patch("logging.getLogger", side_effect=fake_get_logger):
        logger = cf_logging.ConfFlowLogger()

    assert "console" in logger.handlers
    cf_logging.ConfFlowLogger.set_embedded_mode(True)
    assert "console" not in logger.handlers
    cf_logging.ConfFlowLogger.set_embedded_mode(False)
    logger.close()


def test_add_console_handler_idempotent():
    _reset_logger_singleton()
    root_mock = MagicMock()
    root_mock.hasHandlers.return_value = False
    real_get_logger = logging.getLogger

    def fake_get_logger(name=None):
        if name is None:
            return root_mock
        return real_get_logger(name)

    with patch("logging.getLogger", side_effect=fake_get_logger):
        logger = cf_logging.ConfFlowLogger()

    first = logger.handlers["console"]
    logger._add_console_handler()
    assert logger.handlers["console"] is first
    logger.close()


def test_redirect_console_handler_falls_back_to_stream_attr():
    _reset_logger_singleton()
    root_mock = MagicMock()
    root_mock.hasHandlers.return_value = False
    real_get_logger = logging.getLogger

    def fake_get_logger(name=None):
        if name is None:
            return root_mock
        return real_get_logger(name)

    with patch("logging.getLogger", side_effect=fake_get_logger):
        logger = cf_logging.ConfFlowLogger()

    handler = logger.handlers["console"]
    handler.setStream = MagicMock(side_effect=AttributeError("no setStream"))
    stream = io.StringIO()

    logger.redirect_console_handler(stream)

    assert handler.stream is stream
    logger.close()


def test_add_file_handler_skipped_in_embedded_mode(tmp_path):
    _reset_logger_singleton()
    logger = cf_logging.ConfFlowLogger.__new__(cf_logging.ConfFlowLogger)
    logger.logger = logging.getLogger("confflow")
    logger.handlers = {}
    cf_logging.ConfFlowLogger._instance = logger
    cf_logging.ConfFlowLogger._initialized = True
    cf_logging.ConfFlowLogger._embedded_mode = True

    logger.add_file_handler(str(tmp_path / "ignored.log"))

    assert logger.handlers == {}


def test_add_file_handler_replaces_existing_and_set_level(tmp_path):
    _reset_logger_singleton()
    root_mock = MagicMock()
    root_mock.hasHandlers.return_value = False
    real_get_logger = logging.getLogger

    def fake_get_logger(name=None):
        if name is None:
            return root_mock
        return real_get_logger(name)

    with patch("logging.getLogger", side_effect=fake_get_logger):
        logger = cf_logging.ConfFlowLogger()

    first = tmp_path / "first.log"
    second = tmp_path / "second.log"
    logger.add_file_handler(str(first))
    file_handler_1 = logger.handlers["file"]
    logger.add_file_handler(str(second))
    file_handler_2 = logger.handlers["file"]

    assert file_handler_1 is not file_handler_2
    logger.set_level(logging.ERROR)
    assert logger.logger.level == logging.ERROR
    assert all(handler.level == logging.ERROR for handler in logger.handlers.values())
    logger.close()


def test_convenience_methods_delegate_to_underlying_logger():
    _reset_logger_singleton()
    logger = cf_logging.ConfFlowLogger.__new__(cf_logging.ConfFlowLogger)
    logger.logger = MagicMock()
    logger.handlers = {}
    cf_logging.ConfFlowLogger._instance = logger
    cf_logging.ConfFlowLogger._initialized = True

    logger.debug("d")
    logger.info("i")
    logger.warning("w")
    logger.error("e")
    logger.critical("c")
    logger.exception("x")

    logger.logger.debug.assert_called_once_with("d")
    logger.logger.info.assert_called_once_with("i")
    logger.logger.warning.assert_called_once_with("w")
    logger.logger.error.assert_called_once_with("e")
    logger.logger.critical.assert_called_once_with("c")
    logger.logger.exception.assert_called_once_with("x")


def test_redirect_logging_streams_updates_root_and_confflow_handlers():
    _reset_logger_singleton()
    confflow_logger = logging.getLogger("confflow")
    root_logger = logging.getLogger()

    class _AttrOnlyHandler(logging.StreamHandler):
        def setStream(self, stream):
            raise AttributeError("no setStream")

    confflow_handler = _AttrOnlyHandler(io.StringIO())
    root_handler = _AttrOnlyHandler(io.StringIO())
    confflow_logger.addHandler(confflow_handler)
    root_logger.addHandler(root_handler)

    try:
        new_stream = io.StringIO()
        cf_logging.redirect_logging_streams(new_stream, include_root=True)
        assert confflow_handler.stream is new_stream
        assert root_handler.stream is new_stream
    finally:
        confflow_logger.removeHandler(confflow_handler)
        root_logger.removeHandler(root_handler)
