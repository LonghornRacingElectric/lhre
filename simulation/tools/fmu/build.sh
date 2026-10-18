#!/bin/sh
set -eu

work=out/fmu/build
src=$work/OrionFMI
fmu=out/fmu/OrionFMI.fmu

rm -rf "$work"
mkdir -p "$work"
# BobSim has BobLib in engines/boblib since its PR #82, in _0_Utils/external before.
boblib=bobsim/engines/boblib/BobLib
[ -d "$boblib" ] || boblib=bobsim/_0_Utils/external/BobLib/BobLib
cp -R "$boblib" "$work/BobLib"
python3 tools/fmu/records.py "$work"
omc tools/fmu/orion_fmi.mos
test -f "$work/OrionFMI.fmu" || { echo "omc did not write $work/OrionFMI.fmu"; exit 1; }

python3 -m zipfile -e "$work/OrionFMI.fmu" "$src"
python3 tools/fmu/split_init.py "$src/sources/OrionFMI_init_fmu.c"
python3 tools/fmu/patch_runtime.py "$src/sources"
cmake -S "$src/sources" -B "$src/sources/build" -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=clang
cmake --build "$src/sources/build" --parallel "$(nproc)" --target install

rm -f "$fmu"
cd "$src"
python3 -m zipfile -c "../../OrionFMI.fmu" modelDescription.xml binaries resources
cd - >/dev/null
echo "wrote $fmu ($(du -h "$fmu" | cut -f1))"
