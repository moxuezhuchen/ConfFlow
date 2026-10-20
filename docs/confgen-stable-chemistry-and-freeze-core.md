# ConfGen stable chemistry and frozen cores

ConfGen keeps the stable molecular graph separate from reaction geometry. Use
`topology.bonds` for bonds that exist in the input chemistry, including their
bond orders, and `topology.reaction_pairs` for pairs whose geometry is relevant
to the reaction. A reaction pair does not create a covalent bond or change the
stable valence graph. The `FORMING` and `BREAKING` edge kinds remain accepted as
legacy reaction relations; regenerate older adapter-produced inputs if the
adapter had relabeled existing bonds as `FORMING`.

For authoritative stable-graph perception, provide a formal charge for every
atom and a bond order for every stable covalent edge. The input structure must
also carry the matching total charge and multiplicity. The current authoritative
path is limited to closed-shell singlets that pass RDKit valence sanitization;
it does not infer radicals or open-shell electronic states. Partial per-atom
charge declarations are rejected. Inputs without formal-charge declarations
continue through legacy geometry-based perception; bond-order-only metadata is
preserved but does not claim to establish authoritative electronic chemistry.

Minimal closed-shell water example (indices are zero-based):

```yaml
schema_version: 4
index_base: 0
seed: 17
topology:
  bonds:
    - atoms: [0, 1]
      kind: COVALENT
      bond_order: 1.0
    - atoms: [0, 2]
      kind: COVALENT
      bond_order: 1.0
  reaction_pairs:
    - [1, 2]
  atoms:
    - {index: 0, formal_charge: 0}
    - {index: 1, formal_charge: 0}
    - {index: 2, formal_charge: 0}
search:
  freeze_core: [0]
```

`search.freeze_core` selects atoms fixed at their input reference coordinates.
The same indices are used by start generation, search relaxations, release
relaxations and Hessian calculations. Distance restraints are separate and do
not fix coordinates. A backend must advertise fixed-atom support; ConfGen also
checks the returned coordinates and reports a core-displacement failure if a
backend moves a selected atom beyond `1e-5 Å`. The request coordinates must
match the immutable reference within `1e-8 Å`. This output check matters for xTB
builds that accept but ignore `$fix` in some optimization paths.

For a reactive tetrahedral center outside its stable-neighbor tetrahedron, ordinary ETKDG
center-volume conditions can conflict with frozen TS coordinates. The global and local
embedding copies now share the same preparation: they add missing reaction ZERO edges and
remove the internal atom chiral tag only if this nonstandard center and all four stable
neighbors are exactly frozen. The stable molecule, other stereocenters, ETKDG chirality checking
and the independent output stereo audit remain in force. DG results report the affected atom
indices in `embedding_relaxed_centers`. This is not permission to invert a reaction center.

Tetrahedral configuration preservation is always on and needs no CIP or RDKit stereo perception.
Every non-metal atom with exactly four neighbours in the transition-state bonding graph (stable
bonds plus declared reaction pairs) keeps the signed volume of its input neighbour tetrahedron,
keyed by atom index. This covers new reactive centres and existing stereocentres alike, and
conformational changes that do not break bonds cannot invert it. Skipped: atoms with two or more
same-element terminal neighbours (CH2, CH3, CF2), metal centres (governed by `arrangement`; `search` explores other donor arrangements, `keep` holds the input one), and
reference centres whose neighbours are nearly coplanar (unit volume below 0.1; reported only as
`flat_reference`). DG embedding (global and local) labels the remaining centres with their input
chirality, so only the input configuration is generated. A reactive centre lying outside its
neighbour tetrahedron, which ETKDG cannot satisfy, is not labelled and is only filtered after
generation (`filter_only`). Stats appear under `tetrahedral_stereo` (`states`, `embedding_enforced`,
`filter_only`, `flat_reference`, 0-based indices); post-generation rejections are counted as
`generated_tetrahedral_stereo` and re-embedding failures carry the reason `tetrahedral_stereo`.
Audit failures count under the existing `stereo` check. Atoms with more than four neighbours,
three-neighbour invertible atoms (phosphines, sulfoxides) and conformational chirality are outside
this rule. Run with an input structure of the other configuration to obtain it.

`search.ez_policy: preserve` is the default and currently the only supported double-bond state
policy. Genuinely stereogenic stable double bonds acquire indexed substituent quartets from
the stable molecule and reference coordinates. Global/local generated candidates and optimized
search/release outputs must retain their reference side relationship. Opposite or undefined
states fail `double_bond_stereo`. E/Z states also remain distinct during serial and parallel
minimum deduplication, regardless of the geometric RMSD threshold. An `enumerate` request is
rejected; exhaustive E/Z state enumeration is not implemented or implied.

`search.fixed_states` declares torsions whose reference side every structure keeps. Each entry
is four distinct atoms in bonding order, using the step's `index_base`:

```yaml
schema_version: 4
index_base: 1
search:
  fixed_states:
    - [5, 2, 3, 9]    # e.g. three arm torsions of a tripod ligand
    - [6, 2, 4, 10]
    - [7, 2, 1, 11]
```

The reference dihedral θ0 of each entry sets its kind. If `|sin θ0| >= 0.5` it is a `hand`
state (helix, atropisomer, gauche±) and its sign of sin is kept, so a mirror image fails. Otherwise
it is a `side` state (amide or ester s-cis/s-trans) and its sign of cos is kept, which a mirror
image does not change. A candidate exactly on the boundary fails, and a degenerate reference
(coincident or collinear atoms) is refused. The explorer filters generated starts and moves
before xTB (`generated_fixed_state`), audits every relaxed structure (`fixed_state`, release
outputs included), and so publishes only structures that keep the declared states. ETKDG cannot
generate a declared torsion state directly, so each declared element roughly halves the usable DG
starts. Undeclared torsions are still explored freely. Stats appear under `fixed_states`
(`states`, `generated_rejected`, `audit_rejected`, `lowest_audit_rejected_kcal`).

The reminder layer is advisory. `stats["state_changes"]` lists heavy-atom torsions that the final
minima hold on the other side of the same rule, scanned over every final minimum including those
outside the energy window. A unit is a discrete two-state torsion: some minima flip, and every
minimum stays at least 20° from the boundary. Units that flip in identical sets of minima are
grouped, and atom labels are relabelled by graph automorphisms. The WARNING
`confgen_conformational_states` lists the units with ready-to-paste `search.fixed_states` entries,
shifted by `index_base`. Geometry cannot tell barriers, so fast rotors (methoxy gauche±) may be
reported, and a unit only appears when the other state was sampled. Metal-centred Δ/Λ is handled
by `arrangement` (`keep` and `search` both keep the metal's hand), not by this scan.

Search statistics distinguish generation from optimization. For each arm, `generation` reports
actual generation `attempts`, `generated` raw candidates, `failures`, worker CPU seconds,
summed worker wall seconds, map wall seconds and the number sent to optimization (`optimized`).
Worker CPU measures generation in its own process; it excludes optimizer CPU, and summed
worker wall time differs from parallel elapsed map time. Single/lowmode generation is measured
in the coordinator; lowmode wall time includes Hessian waiting while its backend CPU remains
outside this CPU counter. Existing `spent` counters continue to count round optimization calls.
Zero `spent.reembed` therefore does not mean zero re-embedding cost.

For zero-output failures, the public diagnostic code remains
`confgen_no_passing_structures`. Its details carry `outcome_class` and a specific `reason_code`
for backend failure, chemical rejection, core displacement or truncation; existing callers
can keep using the general code without losing the more precise audit reason.

Local re-embedding allocation is reduced by measured generation success and CPU cost relative
to global DG. Four consecutive failed attempts trigger exponential round backoff, with one
task per recovery probe; successful generation resets the backoff. Four failed backoff levels
disable that arm for the rest of this run. `reembed_scheduler` reports this state. Failed
generation does not consume the relaxation-count budget, but can no longer cause unlimited
repeated full allocations. CPU-based allocation can vary with execution timings.

The regression controls reproduce six frozen epoxidation starts and three Ti local regions.
They establish generation and audit correctness for these inputs, not improved full-search
coverage, TS validation, or a guaranteed end-to-end speedup.
