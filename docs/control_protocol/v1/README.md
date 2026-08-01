# Control protocol schema bundle v1

`common.schema.json` defines shared identifiers, locators, state, errors, and
artifacts. `requests.schema.json` and `responses.schema.json` define the eight
operation messages. `input-manifest.schema.json` defines the ordered input file
set referenced by `prepare`. The same bundle is installed by the wheel under
`share/confflow/control_protocol/v1` and is loaded by the control adapter when
the source-tree copy is unavailable.

The `_schema` member in files under `tests/fixtures/control_protocol/v1` is
fixture-inventory metadata only. It is removed before validation and must not be
sent as part of a wire request or response.
