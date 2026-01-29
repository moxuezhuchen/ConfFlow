#!/usr/bin/env bash
set -euo pipefail

# Fake ORCA runner for ConfFlow E2E tests.
# Called as: <orca_path> <inputfile.inp>
# - Writes stdout (captured to .out by calc.py)
# - Writes <basename>.xyz in CWD for parse_last_geometry()

inp="$1"
base="${inp%.inp}"

# Extract xyz block from ORCA input
# Format in template:
#   * xyz <charge> <mult>
#   <coordinates>
#   *
coords=$(awk '
  BEGIN{inblk=0}
  /^\* xyz /{inblk=1; next}
  /^\*\s*$/{if(inblk){exit} }
  { if(inblk){print} }
' "$inp")

# Build XYZ file for calc.py to pick up
natoms=$(printf "%s\n" "$coords" | awk 'NF>=4{n++} END{print n+0}')
{
  echo "$natoms"
  echo "Fake ORCA geometry"
  printf "%s\n" "$coords" | awk 'NF>=4{printf "%s %s %s %s\n", $1,$2,$3,$4}'
} > "${base}.xyz"

# Emit minimal ORCA-like output to stdout (captured as ${base}.out)
cat <<'EOF'
****ORCA TERMINATED NORMALLY****
FINAL SINGLE POINT ENERGY      -123.456789
EOF
