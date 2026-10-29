"""Check combined identities and named allowlists against PR #197."""

import json
import tempfile
from pathlib import Path

import pytest

ROOT = Path(tempfile.mkdtemp(prefix="confflow-pr197-extra."))


def main():
    from tests.v4 import test_arch_review_1029_confgen_xtb_identity as cg
    from tests.v4 import test_arch_review_1029_resume_identity as h

    report = {"root": str(ROOT), "combined_axes": []}
    for state in ("pending", "running_dead", "interrupted"):
        for axis, override in (
            ("input", {"work_item_digest": "sha256:" + "6" * 64}),
            ("definition", {"step_semantic_digest": "sha256:" + "5" * 64}),
            ("provenance", {"producer_provenance": {"adapter_version": "old.v0"}}),
        ):
            root = ROOT / f"{state}-{axis}"
            root.mkdir()
            case = h._case(root)
            h._seed(root, case, state, **override)
            before = h._state(case)
            recorder = h._Recorder()
            error = None
            status = None
            try:
                result = h._run(case, case.req_b, recorder, "resume-B")
                status = result.status.value
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            report["combined_axes"].append(
                dict(
                    state=state,
                    changed_axis=axis,
                    status=status,
                    error=error,
                    executed=recorder.calls,
                    identity_overwritten=h._state(case)["identity"] != before["identity"],
                )
            )
    root = ROOT / "named-allowlist"
    root.mkdir()
    with pytest.MonkeyPatch.context() as mp:
        paths = cg.two_xtb.__wrapped__(root, mp)
        document = cg._document(paths, allowed=Path("xtb"))
        text = cg.toy_text.__wrapped__()
        try:
            result = cg._run(document, root / "run", text)
            report["named_allowlist"] = dict(
                status=result.status, launches=cg._launches(paths["record"])
            )
        except Exception as exc:
            report["named_allowlist"] = dict(
                error=f"{type(exc).__name__}: {exc}", launches=cg._launches(paths["record"])
            )
    (ROOT / "results.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
