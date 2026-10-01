"""The public authoring transport rejects malformed intent structurally."""

import json

import pytest
from jsonschema import Draft202012Validator

from confflow.producer.authoring import dispatch_request
from confflow.producer.boundary import authoring_protocol_schema


@pytest.mark.parametrize("parameters", [{}, {"intent": []}, {"intent": {}, "machine_profile": []}])
def test_malformed_intent_has_valid_error_envelope(parameters):
    response = dispatch_request(
        json.dumps(
            {
                "content_schema": "confflow.authoring.v4",
                "operation": "compile_intent",
                "parameters": parameters,
            }
        )
    )
    assert not response["ok"]
    assert response["operation"] == "compile_intent"
    assert response["diagnostics"][0]["severity"] == "error"
    Draft202012Validator(authoring_protocol_schema()["response"]).validate(response)
