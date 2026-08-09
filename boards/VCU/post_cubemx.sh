#!/bin/sh
# Run after every CubeMX "Generate Code" for this board.
#
# Core/ is entirely CubeMX-owned; hand-written bring-up lives in Board/.
# CubeMX always regenerates Core/Src/main.c, but our entry point is
# Board/main.cpp, so the generated one must go. Core/Inc/main.h stays:
# generated files (stm32g4xx_it.c, stm32g4xx_hal_msp.c) include it.
set -eu
cd "$(dirname "$0")"
rm -f Core/Src/main.c
echo "Removed generated Core/Src/main.c (entry point is Board/main.cpp)."
