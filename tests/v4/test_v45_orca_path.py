#!/usr/bin/env python3

"""V4-5 ORCA reaction-path and NEB helpers: rendering goldens and parsing.

Covers :mod:`confflow.programs.orca.path` and
:mod:`confflow.programs.orca.neb` with crafted fixtures only (no subprocess,
no adapter, no legacy imports):

- block-rendering goldens plus unknown-key, bad-count, and unsupported
  direction rejections (all ``native_input_error``);
- endpoint parsing including shuffle invariance, missing/duplicate banners,
  and strict section rejections;
- NEB image parsing including out-of-order images, missing energy, count
  mismatch, duplicate images, and banner-total mismatches;
- TS-candidate banner gating (a path-maximum image without the banner yields
  ``None``);
- trajectory facts including truncation detection and best-effort skips.
"""

from __future__ import annotations

import pytest

from confflow.programs.orca.neb import (
    NEB_TS_BANNER,
    SUPPORTED_NEB_KEYS,
    parse_neb_images,
    parse_neb_ts_candidate,
    render_neb_blocks,
)
from confflow.programs.orca.path import (
    IRC_FORWARD_BANNER,
    IRC_REVERSE_BANNER,
    IRC_TRUNCATED_MARKER,
    SUPPORTED_IRC_KEYS,
    parse_path_endpoints,
    path_trajectory_facts,
    render_irc_blocks,
)

WATER_ATOMS = ("O", "H", "H")
FORWARD_COORDS = (
    (0.000000, 0.000000, 0.100000),
    (0.760000, 0.590000, 0.000000),
    (0.760000, -0.590000, 0.000000),
)
REVERSE_COORDS = (
    (0.000000, 0.000000, -0.100000),
    (0.750000, 0.600000, 0.000000),
    (0.750000, -0.600000, 0.000000),
)
FORWARD_ENERGY = -76.123456
REVERSE_ENERGY = -76.111111


def _coord_lines(coords: tuple[tuple[float, float, float], ...]) -> str:
    """Format water coordinates as ``<symbol> <x> <y> <z>`` lines."""
    return "\n".join(
        f"{symbol} {x:.6f} {y:.6f} {z:.6f}" for symbol, (x, y, z) in zip(WATER_ATOMS, coords)
    )


def _endpoint_section(
    banner: str,
    *,
    energy: float,
    converged: str = "true",
    coords: tuple[tuple[float, float, float], ...] = FORWARD_COORDS,
    point: int | None = None,
) -> str:
    """Build one IRC endpoint section in the minimal dialect."""
    lines = [banner, f"ENERGY {energy}", f"CONVERGED {converged}"]
    if point is not None:
        lines.append(f"POINT {point}")
    lines += ["GEOMETRY", _coord_lines(coords), "END GEOMETRY"]
    return "\n".join(lines) + "\n"


def _image_section(ordinal: int, total: int, *, energy: float | None, shift: float = 0.0) -> str:
    """Build one NEB image section in the minimal dialect."""
    coords = tuple((x + shift, y, z) for x, y, z in FORWARD_COORDS)
    lines = [f"CONFFLOW NEB IMAGE {ordinal} OF {total}"]
    if energy is not None:
        lines.append(f"ENERGY {energy}")
    lines += ["GEOMETRY", _coord_lines(coords), "END GEOMETRY"]
    return "\n".join(lines) + "\n"


def _ts_section(*, energy: float | None = -76.05) -> str:
    """Build the NEB-TS candidate section in the minimal dialect."""
    lines = [NEB_TS_BANNER]
    if energy is not None:
        lines.append(f"ENERGY {energy}")
    lines += ["GEOMETRY", _coord_lines(FORWARD_COORDS), "END GEOMETRY"]
    return "\n".join(lines) + "\n"


class TestAllowlistContracts:
    """Supported-key allowlists are exact and documented."""

    def test_irc_allowlist(self) -> None:
        assert SUPPORTED_IRC_KEYS == frozenset({"direction", "max_iter"})

    def test_neb_allowlist(self) -> None:
        assert SUPPORTED_NEB_KEYS == frozenset({"n_images", "neb_ts"})


class TestRenderIrcBlocks:
    """Goldens and fail-closed rejections for ``%geom`` IRC rendering."""

    def test_empty_native_renders_empty_geom_block(self) -> None:
        assert render_irc_blocks({}) == "%geom\nend\n"

    def test_direction_both_with_max_iter_golden(self) -> None:
        assert render_irc_blocks({"direction": "both", "max_iter": 200}) == (
            "%geom\n  MaxIter 200\nend\n"
        )

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            render_irc_blocks({"direction": "both", "StepSize": 0.1})

    def test_forward_only_rejected_as_unsupported(self) -> None:
        with pytest.raises(ValueError, match="native_input_error.*unsupported"):
            render_irc_blocks({"direction": "forward"})

    def test_reverse_only_rejected_as_unsupported(self) -> None:
        with pytest.raises(ValueError, match="native_input_error.*unsupported"):
            render_irc_blocks({"direction": "reverse"})

    def test_empty_direction_rejected(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            render_irc_blocks({"direction": ""})

    @pytest.mark.parametrize("bad", [0, -3, "200", 2.5, True])
    def test_bad_max_iter_rejected(self, bad: object) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            render_irc_blocks({"max_iter": bad})

    def test_none_max_iter_means_absent(self) -> None:
        assert render_irc_blocks({"max_iter": None}) == "%geom\nend\n"

    def test_non_mapping_native_rejected(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            render_irc_blocks(["direction"])  # type: ignore[arg-type]


class TestRenderNebBlocks:
    """Goldens and fail-closed rejections for ``%neb`` rendering."""

    def test_minimal_golden(self) -> None:
        assert render_neb_blocks({"n_images": 7}, product_xyz_name="product.xyz") == (
            '%neb\n  NImages 7\n  NEB_End_XYZFile "product.xyz"\nend\n'
        )

    def test_neb_ts_true_adds_comment_not_directive(self) -> None:
        text = render_neb_blocks({"n_images": 3, "neb_ts": True}, product_xyz_name="product.xyz")
        assert text.startswith("# ")
        assert "NEB-TS" in text.splitlines()[0]
        assert "%neb\n  NImages 3\n" in text
        assert 'NEB_End_XYZFile "product.xyz"' in text

    def test_neb_ts_false_renders_plain_block(self) -> None:
        text = render_neb_blocks({"n_images": 3, "neb_ts": False}, product_xyz_name="product.xyz")
        assert not text.startswith("#")
        assert text.startswith("%neb")

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            render_neb_blocks({"n_images": 5, "ClimbingImage": True}, product_xyz_name="p.xyz")

    def test_missing_n_images_rejected(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            render_neb_blocks({}, product_xyz_name="product.xyz")

    @pytest.mark.parametrize("bad", [0, 1, 2, -5, "7", 7.0, True, None])
    def test_bad_n_images_rejected(self, bad: object) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            render_neb_blocks({"n_images": bad}, product_xyz_name="product.xyz")

    def test_non_bool_neb_ts_rejected(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            render_neb_blocks({"n_images": 5, "neb_ts": "yes"}, product_xyz_name="p.xyz")

    @pytest.mark.parametrize("bad", ["", "   ", "product", 'a"b.xyz', "a\nb.xyz"])
    def test_bad_product_xyz_name_rejected(self, bad: str) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            render_neb_blocks({"n_images": 5}, product_xyz_name=bad)

    def test_non_mapping_native_rejected(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            render_neb_blocks(["n_images"], product_xyz_name="p.xyz")  # type: ignore[arg-type]


class TestParsePathEndpoints:
    """Strict endpoint parsing over the minimal IRC dialect."""

    def _happy_text(self) -> str:
        """Build a two-endpoint fixture in canonical banner order."""
        forward = _endpoint_section(
            IRC_FORWARD_BANNER, energy=FORWARD_ENERGY, coords=FORWARD_COORDS, point=12
        )
        reverse = _endpoint_section(
            IRC_REVERSE_BANNER,
            energy=REVERSE_ENERGY,
            converged="false",
            coords=REVERSE_COORDS,
            point=9,
        )
        return forward + reverse

    def test_happy_path_facts(self) -> None:
        forward, reverse = parse_path_endpoints(self._happy_text(), atoms=WATER_ATOMS)
        assert (forward.direction, reverse.direction) == ("forward", "reverse")
        assert forward.energy_hartree == pytest.approx(FORWARD_ENERGY)
        assert reverse.energy_hartree == pytest.approx(REVERSE_ENERGY)
        assert forward.converged is True
        assert reverse.converged is False
        assert forward.point_ordinal == 12
        assert reverse.point_ordinal == 9
        assert forward.geometry.atoms == WATER_ATOMS
        assert reverse.geometry.atoms == WATER_ATOMS
        assert forward.geometry.coordinates[0] == pytest.approx(FORWARD_COORDS[0])
        assert reverse.geometry.coordinates[0] == pytest.approx(REVERSE_COORDS[0])

    def test_shuffled_banner_order_gives_same_output(self) -> None:
        forward = _endpoint_section(
            IRC_FORWARD_BANNER, energy=FORWARD_ENERGY, coords=FORWARD_COORDS, point=12
        )
        reverse = _endpoint_section(
            IRC_REVERSE_BANNER,
            energy=REVERSE_ENERGY,
            converged="false",
            coords=REVERSE_COORDS,
            point=9,
        )
        canonical = parse_path_endpoints(forward + reverse, atoms=WATER_ATOMS)
        shuffled = parse_path_endpoints(reverse + forward, atoms=WATER_ATOMS)
        assert shuffled == canonical
        assert [endpoint.direction for endpoint in shuffled] == ["forward", "reverse"]

    def test_missing_point_ordinal_defaults_to_none(self) -> None:
        text = _endpoint_section(
            IRC_FORWARD_BANNER, energy=FORWARD_ENERGY, coords=FORWARD_COORDS
        ) + _endpoint_section(IRC_REVERSE_BANNER, energy=REVERSE_ENERGY, coords=REVERSE_COORDS)
        forward, _ = parse_path_endpoints(text, atoms=WATER_ATOMS)
        assert forward.point_ordinal is None

    def test_missing_forward_banner_rejected(self) -> None:
        text = _endpoint_section(IRC_REVERSE_BANNER, energy=REVERSE_ENERGY, coords=REVERSE_COORDS)
        with pytest.raises(ValueError, match="forward banner"):
            parse_path_endpoints(text, atoms=WATER_ATOMS)

    def test_missing_reverse_banner_rejected(self) -> None:
        text = _endpoint_section(IRC_FORWARD_BANNER, energy=FORWARD_ENERGY, coords=FORWARD_COORDS)
        with pytest.raises(ValueError, match="reverse banner"):
            parse_path_endpoints(text, atoms=WATER_ATOMS)

    def test_duplicate_banner_rejected(self) -> None:
        text = self._happy_text() + _endpoint_section(
            IRC_FORWARD_BANNER, energy=FORWARD_ENERGY, coords=FORWARD_COORDS
        )
        with pytest.raises(ValueError, match="exactly once"):
            parse_path_endpoints(text, atoms=WATER_ATOMS)

    def test_missing_energy_rejected(self) -> None:
        text = self._happy_text().replace(f"ENERGY {FORWARD_ENERGY}\n", "")
        with pytest.raises(ValueError, match="ENERGY"):
            parse_path_endpoints(text, atoms=WATER_ATOMS)

    def test_invalid_converged_flag_rejected(self) -> None:
        text = self._happy_text().replace("CONVERGED false", "CONVERGED maybe", 1)
        with pytest.raises(ValueError, match="converged flag"):
            parse_path_endpoints(text, atoms=WATER_ATOMS)

    def test_unknown_section_line_rejected(self) -> None:
        text = self._happy_text().replace("POINT 12\n", "POINT 12\nSTEP 0.1\n", 1)
        with pytest.raises(ValueError, match="unknown line"):
            parse_path_endpoints(text, atoms=WATER_ATOMS)

    def test_atom_symbol_mismatch_rejected(self) -> None:
        text = self._happy_text().replace("O 0.000000", "N 0.000000", 1)
        with pytest.raises(ValueError, match="expected"):
            parse_path_endpoints(text, atoms=WATER_ATOMS)

    def test_atom_count_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError, match="atoms"):
            parse_path_endpoints(self._happy_text(), atoms=("O", "H"))

    def test_stray_line_before_first_banner_rejected(self) -> None:
        with pytest.raises(ValueError, match="outside IRC endpoint sections"):
            parse_path_endpoints("SOME HEADER\n" + self._happy_text(), atoms=WATER_ATOMS)

    def test_trajectory_lines_before_first_banner_coexist(self) -> None:
        text = "IRC POINT 1 FORWARD ENERGY -76.1\n" + self._happy_text()
        forward, _ = parse_path_endpoints(text, atoms=WATER_ATOMS)
        assert forward.energy_hartree == pytest.approx(FORWARD_ENERGY)


class TestParseNebImages:
    """Image parsing keyed by native ordinals, never encounter order."""

    def test_out_of_order_images_sorted_by_member_index(self) -> None:
        text = (
            _image_section(3, 3, energy=-76.09, shift=0.3)
            + _image_section(1, 3, energy=-76.12, shift=0.1)
            + _image_section(2, 3, energy=-76.10, shift=0.2)
        )
        members = parse_neb_images(text, atoms=WATER_ATOMS, n_images=3)
        assert [member.member_index for member in members] == [1, 2, 3]
        assert [member.energy_hartree for member in members] == pytest.approx(
            [-76.12, -76.10, -76.09]
        )
        assert members[0].geometry.coordinates[0][0] == pytest.approx(FORWARD_COORDS[0][0] + 0.1)
        assert all(member.geometry.atoms == WATER_ATOMS for member in members)

    def test_missing_energy_parses_as_none_never_zero(self) -> None:
        text = (
            _image_section(1, 3, energy=-76.12)
            + _image_section(2, 3, energy=None)
            + _image_section(3, 3, energy=-76.09)
        )
        members = parse_neb_images(text, atoms=WATER_ATOMS, n_images=3)
        assert members[1].energy_hartree is None
        assert members[1].energy_hartree != 0.0

    def test_count_mismatch_rejected(self) -> None:
        text = _image_section(1, 3, energy=-76.12) + _image_section(2, 3, energy=-76.10)
        with pytest.raises(ValueError, match="!= n_images"):
            parse_neb_images(text, atoms=WATER_ATOMS, n_images=3)

    def test_duplicate_image_rejected(self) -> None:
        text = (
            _image_section(1, 3, energy=-76.12)
            + _image_section(1, 3, energy=-76.11)
            + _image_section(2, 3, energy=-76.10)
            + _image_section(3, 3, energy=-76.09)
        )
        with pytest.raises(ValueError, match="more than once"):
            parse_neb_images(text, atoms=WATER_ATOMS, n_images=3)

    def test_banner_total_mismatch_rejected(self) -> None:
        text = (
            _image_section(1, 4, energy=-76.12)
            + _image_section(2, 4, energy=-76.10)
            + _image_section(3, 4, energy=-76.09)
        )
        with pytest.raises(ValueError, match="disagrees with n_images"):
            parse_neb_images(text, atoms=WATER_ATOMS, n_images=3)

    def test_out_of_range_ordinal_rejected(self) -> None:
        text = (
            _image_section(0, 3, energy=-76.12)
            + _image_section(1, 3, energy=-76.11)
            + _image_section(2, 3, energy=-76.10)
        )
        with pytest.raises(ValueError, match="out of range"):
            parse_neb_images(text, atoms=WATER_ATOMS, n_images=3)

    def test_ts_section_skipped_by_image_parser(self) -> None:
        text = (
            _image_section(1, 2, energy=-76.12)
            + _ts_section()
            + _image_section(2, 2, energy=-76.10)
        )
        members = parse_neb_images(text, atoms=WATER_ATOMS, n_images=2)
        assert [member.member_index for member in members] == [1, 2]

    def test_bad_n_images_parameter_rejected(self) -> None:
        with pytest.raises(ValueError, match="positive integer"):
            parse_neb_images("", atoms=WATER_ATOMS, n_images=0)


class TestParseNebTsCandidate:
    """TS-candidate parsing is gated on the explicit optimized-TS banner."""

    def test_banner_with_geometry_returns_candidate(self) -> None:
        member = parse_neb_ts_candidate(_ts_section(energy=-76.05))
        assert member is not None
        assert member.member_index == 0
        assert member.energy_hartree == pytest.approx(-76.05)
        assert member.geometry.atoms == WATER_ATOMS
        assert member.metadata["neb_ts_candidate"] is True
        assert member.metadata["member_index_fallback"] is True

    def test_banner_without_energy_returns_none_energy(self) -> None:
        member = parse_neb_ts_candidate(_ts_section(energy=None))
        assert member is not None
        assert member.energy_hartree is None

    def test_path_maximum_without_banner_yields_none(self) -> None:
        text = (
            _image_section(1, 3, energy=-76.12)
            + _image_section(2, 3, energy=-76.05)
            + _image_section(3, 3, energy=-76.11)
        )
        assert parse_neb_ts_candidate(text) is None

    def test_empty_text_yields_none(self) -> None:
        assert parse_neb_ts_candidate("") is None

    def test_banner_without_geometry_rejected(self) -> None:
        with pytest.raises(ValueError, match="GEOMETRY"):
            parse_neb_ts_candidate(NEB_TS_BANNER + "\nENERGY -76.05\n")

    def test_duplicate_banner_rejected(self) -> None:
        with pytest.raises(ValueError, match="duplicate NEB-TS banner"):
            parse_neb_ts_candidate(_ts_section() + _ts_section())


class TestPathTrajectoryFacts:
    """Best-effort trajectory point counts, energies, and truncation."""

    def test_counts_energies_and_truncation(self) -> None:
        text = (
            "IRC POINT 1 FORWARD ENERGY -76.120000\n"
            "IRC POINT 2 FORWARD ENERGY -76.110000\n"
            "IRC POINT 1 REVERSE ENERGY -76.115000\n"
            f"{IRC_TRUNCATED_MARKER}\n"
        )
        facts = path_trajectory_facts(text)
        assert facts["n_points"] == 3
        assert facts["n_forward_points"] == 2
        assert facts["n_reverse_points"] == 1
        assert facts["energies_hartree"] == pytest.approx((-76.12, -76.11, -76.115))
        assert facts["truncated"] is True

    def test_converged_run_is_not_truncated(self) -> None:
        facts = path_trajectory_facts("IRC POINT 1 FORWARD ENERGY -76.1\n")
        assert facts["truncated"] is False
        assert facts["n_points"] == 1

    def test_garbage_lines_skipped_without_raising(self) -> None:
        facts = path_trajectory_facts("not a point line\nIRC POINT x BROKEN\n")
        assert facts["n_points"] == 0
        assert facts["energies_hartree"] == ()
        assert facts["truncated"] is False

    def test_empty_text_reports_zeros(self) -> None:
        facts = path_trajectory_facts("")
        assert facts == {
            "n_points": 0,
            "n_forward_points": 0,
            "n_reverse_points": 0,
            "energies_hartree": (),
            "truncated": False,
        }
