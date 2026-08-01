"""Build provenance populated in the wheel build directory.

The wheel MUST NOT carry its own final-file SHA-256 (``WHEEL_FILENAME``
and ``WHEEL_SHA256`` previously lived here, but that left the wire
protocol with literal placeholder strings like ``"unbound"`` when the
release workflow never injected the environment variables). Real
install provenance — including the wheel digest — now lives in
``<sys.prefix>/share/confflow/install-provenance.json`` and is owned by
:mod:`confflow.install_provenance`. Only the build-time git commit and
working-tree cleanliness are baked into the wheel.
"""

COMMIT: str | None = None
DIRTY: bool | None = None
