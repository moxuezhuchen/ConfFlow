# IS.0 engine-report checkpoint

Engine reports that exist only on (or differ on) the input-simplification branch:

* 5 reports added by IS tests (`test_confgen_paths_audit.py`, `test_confgen_paths_phase0.py`).
* 1 report (`test_pinned_coordination_ring_damage_is_drift`) whose *test input* changed on the IS branch
  (commit 99283a7 replaces a symmetry-tied square seed with the ring lane's own puckered geometry).
  Acceptor evidence: running the IS engine on the *old* test input reproduces the B0.1 report byte for byte,
  so the engine behaviour is unchanged; only the fixture differs.

The B0.1 baseline files are untouched.
