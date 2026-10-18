# Reaction embedding regression inputs

Original atom order, stable chemical graphs and coordinates from the author-mapped
2026-10-19 cause controls in `benchmarks/confgen_racerts_formal_20261018`.
The epoxidation TS reproduces a fully frozen, nonstandard tetrahedral reaction center.
Ti retains the three recorded local regions, parent coordinates and distance targets.
These inputs test geometry generation and auditing, not energies or TS validation.

Literature source: Schmid et al., JCIM 2026, DOI
[10.1021/acs.jcim.5c02794](https://doi.org/10.1021/acs.jcim.5c02794),
[author dataset v2](https://zenodo.org/records/18351126). The fixture retains the
author-mapped stable graph, original TS atom order, and the recorded Ti local-control
parent/regions; energies and full search logs are intentionally absent.
