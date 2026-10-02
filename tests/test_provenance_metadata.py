#!/usr/bin/env python3

"""CID and TS metadata provenance across DB, export and refine."""

from __future__ import annotations

import numpy as np

from confflow.blocks.refine import processor


def test_refine_output_keeps_ts_metadata(tmp_path):
    frames = [
        {
            "natoms": 2,
            "energy": -100.0,
            "energy_key": "G",
            "num_imag_freqs": 1,
            "extra_data": {"CID": "A000001", "G_corr": 1.0, "TSAtoms": "1,2", "TSBond": 0.74},
            "original_atoms": ["H", "H"],
            "coords": np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.74]], dtype=np.float64),
        }
    ]

    out = tmp_path / "out.xyz"
    processor._write_refine_output(str(out), frames, global_min=-100.0)
    text = out.read_text(encoding="utf-8")

    assert "TSAtoms=1,2" in text
    assert "TSBond=0.74" in text
    assert "G_corr=1.0" in text
