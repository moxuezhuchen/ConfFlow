#!/usr/bin/env python3

"""J1 envelope: ``structure_preview`` authoring seam (CF-only, JD deferred).

Covers the thin dispatch over the frozen ``structure_preview_request``
projection: enum single-source, direct-call equivalence (XYZ/GJF/INP),
native ``StructurePreviewError`` attributes carried structurally (never
message-parsed, never collapsed to generic ``invalid_request``), existing
wire fields only (``code=schema_error``, ``reason=<native code>``,
``field_path=parameters.content_text``), ``request_document_digest=None``
with no parameters-digest impersonation, CLI exit/schema behavior, and
old-operation verbatim regression.
"""

from __future__ import annotations

import io
import json
import sys

from jsonschema import Draft202012Validator

from confflow.producer.authoring import AUTHORING_OPERATIONS, dispatch_request
from confflow.producer.boundary import authoring_protocol_schema
from confflow.producer.structure_preview import (
    StructurePreviewError,
    structure_preview_request,
)


def _gjf_minimal(
    atoms: str = "C 0.0 0.0 0.0\nH 0.0 0.0 1.089",
    charge_mult: str = "0 1",
    title: str = "preview",
    route: str = "#p B3LYP/6-31G Opt",
) -> str:
    return f"%chk=job.chk\n{route}\n\n{title}\n\n{charge_mult}\n{atoms}\n\n"


def _xyz_minimal(
    atoms: str = "C 1.23456789 -0.0000015 3.14159265\nH 0.0 0.0 1.00000001",
    comment: str = "comment",
) -> str:
    lines = atoms.strip().splitlines()
    return f"{len(lines)}\n{comment}\n{atoms}\n"


def _inp_minimal(
    atoms: str = "O 0.0 0.0 0.0\nH 0.76 0.59 0.0\nH 0.76 -0.59 0.0",
    header: str = "* xyz 0 1",
    pre: str = "! B3LYP def2-SVP Opt\n",
) -> str:
    return f"{pre}{header}\n{atoms}\n*\n"


def _request(parameters: dict, operation: str = "structure_preview") -> bytes:
    return json.dumps(
        {
            "content_schema": "confflow.authoring.v4",
            "operation": operation,
            "parameters": parameters,
        }
    ).encode("utf-8")


def _validate_response_envelope(response: dict) -> None:
    Draft202012Validator(authoring_protocol_schema()["response"]).validate(response)


def test_operation_enum_adds_structure_preview_single_source() -> None:
    schema = authoring_protocol_schema()
    for side in ("request", "response"):
        enum = schema["request" if side == "request" else "response"]["properties"]["operation"][
            "enum"
        ]
        assert "structure_preview" in enum
    assert "structure_preview" in AUTHORING_OPERATIONS
    for legacy in (
        "describe_step",
        "binding_candidates",
        "instantiate_card",
        "validate_document",
        "check_compatibility",
        "compile_intent",
        "preview_paths",
    ):
        assert legacy in AUTHORING_OPERATIONS
    assert len(AUTHORING_OPERATIONS) == 8


def test_dispatch_success_equals_direct_call_xyz_gjf_inp() -> None:
    cases = [
        ("m.xyz", _xyz_minimal(), "xyz"),
        ("ligand.gjf", _gjf_minimal(), "gaussian-gjf"),
        ("job.inp", _inp_minimal(), "orca-inp"),
    ]
    for filename, text, source_format in cases:
        params = {"filename": filename, "content_text": text}
        direct = structure_preview_request(params)
        response = dispatch_request(_request(params))
        assert response["ok"] is True
        assert response["operation"] == "structure_preview"
        assert response["request_document_digest"] is None
        assert response["diagnostics"] == []
        assert response["result"] == direct
        assert response["result"]["index_base"] == 1
        assert response["result"]["source_format"] == source_format
        _validate_response_envelope(response)


def test_dispatch_verbatim_coordinates_no_unit_conversion() -> None:
    text = _xyz_minimal()
    response = dispatch_request(_request({"filename": "m.xyz", "content_text": text}))
    assert response["result"]["coordinates"][0] == [1.23456789, -0.0000015, 3.14159265]
    gjf = _gjf_minimal(atoms="C 1.23456789 -0.0000015 3.14159265\nH 0.0 0.0 1.00000001")
    response = dispatch_request(_request({"filename": "p.gjf", "content_text": gjf}))
    assert response["result"]["coordinates"][0] == [1.23456789, -0.0000015, 3.14159265]


def test_dispatch_filename_never_opened_only_text_processed() -> None:
    text = _xyz_minimal()
    response = dispatch_request(
        _request({"filename": "/nonexistent/should-never-open.xyz", "content_text": text})
    )
    assert response["ok"] is True
    assert response["result"]["elements"] == ["C", "H"]


def test_dispatch_minimal_request_no_document_digest_impersonation() -> None:
    params = {"filename": "m.xyz", "content_text": _xyz_minimal()}
    bare = dispatch_request(_request(params))
    assert bare["ok"] is True
    assert bare["request_document_digest"] is None
    with_doc = dispatch_request(
        json.dumps(
            {
                "content_schema": "confflow.authoring.v4",
                "operation": "structure_preview",
                "document": {},
                "parameters": params,
            }
        ).encode("utf-8")
    )
    assert with_doc["ok"] is True
    assert with_doc["request_document_digest"] is None
    assert with_doc["result"] == bare["result"]


def test_native_error_attributes_carried_structurally_not_message_parsed() -> None:
    # Root counterexample shape: non-ASCII filename, native line 1.
    text = "# hf/sto-3g Units=Bohr\n\nt\n\n0 1\nH 0 0 0\nH 0 0 1.4\n\n"
    params = {"filename": "中文.gjf", "content_text": text}
    try:
        structure_preview_request(params)
    except StructurePreviewError as exc:
        live = exc
    else:  # pragma: no cover - projection must refuse Bohr
        raise AssertionError("Bohr route must raise StructurePreviewError")
    assert live.line == 1
    assert live.source_label == "中文.gjf"
    response = dispatch_request(_request(params))
    assert response["ok"] is False
    assert response["request_document_digest"] is None
    assert response["result"] is None
    (diag,) = response["diagnostics"]
    assert diag["code"] == "schema_error"
    assert diag["reason"] == live.code == "unsupported_unit"
    assert diag["reason"] != "invalid_request"
    assert diag["field_path"] == "parameters.content_text"
    assert diag["message"].startswith("native_input_error:")
    # The wire itself carries every native attribute; compare the response
    # mapping against the live exception item by item (not the exception alone).
    details = diag["details"]
    assert details["native_code"] == live.code
    assert details["line"] == live.line == 1
    assert details["source_label"] == live.source_label == "中文.gjf"
    assert details["field"] == live.field == "content_text"
    assert details["filename"] == live.filename == "中文.gjf"
    assert details["source_format"] == live.source_format == "gaussian-gjf"
    _validate_response_envelope(response)


def test_unknown_suffix_without_hint_and_hint_conflict_structured() -> None:
    text = _xyz_minimal()
    missing = dispatch_request(_request({"filename": "m.unknown", "content_text": text}))
    assert missing["ok"] is False
    assert missing["diagnostics"][0]["reason"] == "invalid_format"
    assert missing["diagnostics"][0]["code"] == "schema_error"
    assert missing["request_document_digest"] is None
    conflict = dispatch_request(
        _request({"filename": "m.xyz", "content_text": text, "source_format_hint": "gaussian-gjf"})
    )
    assert conflict["ok"] is False
    assert conflict["diagnostics"][0]["reason"] == "invalid_format"
    _validate_response_envelope(conflict)


def test_multi_frame_xyz_refused_structured() -> None:
    two = "2\nc1\nH 0 0 0\nH 0 0 1\n2\nc2\nH 0 0 0\nH 0 0 2\n"
    response = dispatch_request(_request({"filename": "m.xyz", "content_text": two}))
    assert response["ok"] is False
    assert response["diagnostics"][0]["reason"] == "multiple_geometries"
    assert response["diagnostics"][0]["code"] == "schema_error"
    _validate_response_envelope(response)


def test_bohr_au_counterexamples_refused_never_converted() -> None:
    routes = [
        "# hf/sto-3g\n Units(Bohr)",
        "# hf/sto-3g\n Units=(Bohr)",
        "# hf/sto-3g Units=(AU)",
        "# hf/sto-3g\n Units=(AU)",
    ]
    for route in routes:
        text = f"%chk=j.chk\n{route}\n\nt\n\n0 1\nH 0 0 0\nH 0 0 1.4\n\n"
        response = dispatch_request(_request({"filename": "h.gjf", "content_text": text}))
        assert response["ok"] is False, route
        assert response["diagnostics"][0]["reason"] == "unsupported_unit"
        assert response["result"] is None
    orca = "! B3LYP Bohr Opt\n* xyz 0 1\nH 0 0 0\nH 0 0 0.74\n*\n"
    response = dispatch_request(_request({"filename": "h.inp", "content_text": orca}))
    assert response["ok"] is False
    assert response["diagnostics"][0]["reason"] == "unsupported_unit"


def test_freeze_style_five_token_line_refused_structured() -> None:
    text = _gjf_minimal(atoms="C -1 0.0 0.0 0.0")
    response = dispatch_request(_request({"filename": "f.gjf", "content_text": text}))
    assert response["ok"] is False
    assert response["diagnostics"][0]["reason"] == "unknown_coordinate_token"
    assert response["diagnostics"][0]["code"] == "schema_error"
    _validate_response_envelope(response)


def test_empty_and_invalid_parameters_structured() -> None:
    empty = dispatch_request(_request({"filename": "e.gjf", "content_text": ""}))
    assert empty["ok"] is False
    assert empty["diagnostics"][0]["code"] == "schema_error"
    missing = dispatch_request(_request({"filename": "m.xyz"}))
    assert missing["ok"] is False
    assert missing["diagnostics"][0]["reason"] == "invalid_parameters"
    bad_op = dispatch_request(b'{"content_schema": "confflow.authoring.v4", "operation": "nope"}')
    assert bad_op["ok"] is False
    assert bad_op["diagnostics"][0]["reason"] == "unsupported_operation"


def test_cli_success_exit0_stdout_schema_and_stdin_preserved() -> None:
    from confflow.v4cli import main

    text = _gjf_minimal(atoms="C 1.23456789 -0.0000015 3.14159265\nH 0.0 0.0 1.00000001")
    payload = _request({"filename": "p.gjf", "content_text": text})
    stream = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
    marvel_stdin, marvel_stdout = sys.stdin, sys.stdout
    captured, err = io.StringIO(), io.StringIO()
    sys.stdin, sys.stdout, sys.stderr = stream, captured, err  # type: ignore[assignment]
    try:
        code = main(["authoring", "--stdin", "--json"])
    finally:
        sys.stdin, sys.stdout, sys.stderr = marvel_stdin, marvel_stdout, sys.stderr
    assert code == 0
    assert "Traceback" not in err.getvalue()
    envelope = json.loads(captured.getvalue())
    assert envelope["ok"] is True
    assert envelope["result"]["coordinates"][0] == [1.23456789, -0.0000015, 3.14159265]
    _validate_response_envelope(envelope)


def test_cli_failure_exit1_no_traceback() -> None:
    from confflow.v4cli import main

    text = _gjf_minimal(route="#p B3LYP/6-31G Opt Units=Bohr")
    payload = _request({"filename": "b.gjf", "content_text": text})
    stream = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
    marvel_stdin, marvel_stdout, marvel_stderr = sys.stdin, sys.stdout, sys.stderr
    captured, err = io.StringIO(), io.StringIO()
    sys.stdin, sys.stdout, sys.stderr = stream, captured, err  # type: ignore[assignment]
    try:
        code = main(["authoring", "--stdin", "--json"])
    finally:
        sys.stdin, sys.stdout, sys.stderr = marvel_stdin, marvel_stdout, marvel_stderr
    assert code == 1
    assert "Traceback" not in captured.getvalue() + err.getvalue()
    envelope = json.loads(captured.getvalue())
    assert envelope["ok"] is False
    assert envelope["diagnostics"][0]["reason"] == "unsupported_unit"
    _validate_response_envelope(envelope)


def test_cli_malformed_utf8_stdin_structured_no_traceback() -> None:
    from confflow.v4cli import main

    stream = io.TextIOWrapper(io.BytesIO(b"\xff\xfe{\x00"), encoding="utf-8")
    marvel_stdin, marvel_stdout, marvel_stderr = sys.stdin, sys.stdout, sys.stderr
    captured, err = io.StringIO(), io.StringIO()
    sys.stdin, sys.stdout, sys.stderr = stream, captured, err  # type: ignore[assignment]
    try:
        code = main(["authoring", "--stdin", "--json"])
    finally:
        sys.stdin, sys.stdout, sys.stderr = marvel_stdin, marvel_stdout, marvel_stderr
    assert code == 1
    assert "Traceback" not in captured.getvalue() + err.getvalue()
    envelope = json.loads(captured.getvalue())
    assert envelope["ok"] is False
    assert envelope["diagnostics"]


def test_old_preview_paths_success_and_fail_verbatim() -> None:
    import pathlib

    case = json.loads(
        (
            pathlib.Path(__file__).resolve().parent.parent
            / "fixtures"
            / "paths_equivalence"
            / "cases.json"
        ).read_text(encoding="utf-8")
    )["hydrogen_cases"][0]
    record = case["record"]
    butane = {
        "id": "preview",
        "atoms": list(record["atoms"]),
        "coordinates": [list(point) for point in record["coordinates"]],
    }
    path = {"start": 1, "end": 4, "move": "end", "angles": [0.0, 120.0, 240.0]}
    ok_payload = json.dumps(
        {
            "content_schema": "confflow.authoring.v4",
            "operation": "preview_paths",
            "parameters": {
                "structure": butane,
                "native": {"schema_version": 3, "index_base": 1, "paths": [path]},
            },
        }
    ).encode("utf-8")
    ok_response = dispatch_request(ok_payload)
    assert ok_response["ok"] is True
    assert ok_response["result"]["raw_conformers"] == 27
    assert ok_response["request_document_digest"] is None
    _validate_response_envelope(ok_response)
    fail_response = dispatch_request(_request({}, operation="preview_paths"))
    assert fail_response["ok"] is False
    assert fail_response["diagnostics"][0]["code"] == "schema_error"
    assert fail_response["diagnostics"][0]["reason"] == "invalid_request"
    assert fail_response["request_document_digest"] is None
    # The global Diagnostic serializer is untouched: old operations project
    # only the existing wire members, never the new-op details member.
    assert "details" not in fail_response["diagnostics"][0]
