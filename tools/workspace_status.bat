@echo off
rem Bazel workspace status script for Windows clients (see
rem --workspace_status_command in .bazelrc). Must emit the same STABLE_* keys
rem as tools/workspace_status.sh; tools/firmware/gen_build_info.py consumes
rem them.
setlocal enabledelayedexpansion

git rev-parse --git-dir >NUL 2>&1
if errorlevel 1 goto :nogit

for /f "delims=" %%i in ('git rev-parse HEAD') do echo STABLE_GIT_SHA %%i
for /f "delims=" %%i in ('git describe --tags --always 2^>NUL ^|^| git rev-parse --short HEAD') do echo STABLE_GIT_DESCRIBE %%i

rem Dirty = uncommitted changes to tracked files. Untracked files don't count.
set DIRTY=0
for /f "delims=" %%i in ('git status --porcelain --untracked-files=no') do set DIRTY=1
echo STABLE_GIT_DIRTY !DIRTY!
exit /b 0

:nogit
rem Not a git checkout (e.g. a source tarball) — stamp placeholders rather
rem than failing the whole build.
echo STABLE_GIT_SHA unknown
echo STABLE_GIT_DESCRIBE unknown
echo STABLE_GIT_DIRTY 0
exit /b 0
