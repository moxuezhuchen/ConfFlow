"""Golden compatibility coverage for durable v2 digest inputs."""

from confflow.calc.artifacts import compute_config_digest, compute_input_digest
from confflow.config.models import CalcStepParams, GlobalOptions
from confflow.workflow.step_handlers import _compute_confgen_step_signature


def test_v2_calc_and_confgen_digest_golden(tmp_path):
    source = tmp_path / "confflow-v2-golden.xyz"
    source.write_text("1\ngolden\nH 0 0 0\n", encoding="utf-8")
    config = CalcStepParams.from_params(
        {"iprog": "orca", "itask": "sp", "keyword": "HF"}, GlobalOptions.from_mapping({})
    )

    assert (
        compute_input_digest(source)
        == "sha256:cb2e57ed3a08c73122149d570597f59cf04296b144f52df3d9cb6ab043db1efc"
    )
    assert (
        compute_config_digest(config)
        == "sha256:a580e0a9e56efc85af55391d3fb887bd77dedc99746636ac9b873c536529a725"
    )
    assert (
        _compute_confgen_step_signature(
            current_input=str(source),
            input_files=[str(source)],
            run_kwargs={"workers": 1, "optimize": False},
            multi_frame=False,
        )
        == "sha256:516f38d9c0a467d0a62f9fc4a5744f12be70b3366f4ef31e61ef7be909e09053"
    )
