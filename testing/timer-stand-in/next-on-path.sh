# Sourced by each stand-in here: REAL is the command of the stand-in's name
# that the PATH finds after this folder.

REAL=$(PATH=$(echo "$PATH" | tr ':' '\n' | grep -vxF "$(cd "$(dirname "$0")" && pwd)" | paste -sd:) \
           command -v "$(basename "$0")")
