#!/usr/bin/env python3
"""Headless start-up smoke for JobDesk: the real main window with no contract.

Acceptor tool.  Builds ``MainWindow`` on ``WorkflowEditorService()`` (what
``gui/app.py::main`` does before a server contract is resolved), opens every
page, then binds and unbinds the authoring fixture contract and checks that the
global rows follow the manifest.  Exit status 0 only if everything holds.

Usage: QT_QPA_PLATFORM=offscreen PYTHONPATH=<jd>/src:<jd> startup_smoke.py
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from jobdesk_v2.application.editor import WorkflowDraftStore, WorkflowEditorService
from jobdesk_v2.gui.main_window import MainWindow
from tests.contract_fixtures import authoritative_contract

PAGES = ("new_run", "workflows", "workflow_editor", "files", "runs", "settings")


def main() -> int:
    app = QApplication.instance() or QApplication([])
    service = WorkflowEditorService()
    store = WorkflowDraftStore(service)
    window = MainWindow(service, store)
    failures: list[str] = []
    for key in PAGES:
        try:
            if not window.show_page(key):
                failures.append(f"show_page({key!r}) returned False")
        except Exception as exc:  # noqa: BLE001 - the smoke reports every failure
            failures.append(f"show_page({key!r}) raised {type(exc).__name__}: {exc}")
    page = window.stack.widget(0)
    section = page.calculation_section
    if sorted(section._global_controls) != []:
        failures.append("global rows exist with no contract")
    service.rebind(authoritative_contract())
    page._refresh()
    if sorted(section._global_controls) != ["global.charge", "global.multiplicity"]:
        failures.append(f"global rows after binding: {sorted(section._global_controls)}")
    service.rebind(WorkflowEditorService().contract)
    page._refresh()
    if sorted(section._global_controls) != []:
        failures.append("global rows remain after unbinding")
    del app
    for line in failures:
        print("FAIL:", line)
    print("startup smoke:", "FAILED" if failures else "ok")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
