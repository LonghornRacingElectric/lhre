#!/usr/bin/env bash
# Prints Markdown for the job summary: each failed Bazel test with the tail of
# its log, so a failure is readable without the full build output.
set -uo pipefail

log=$1
echo "### Failed Bazel tests"
echo
if [ ! -f "$log" ]; then
  echo "No test output. The build failed before the tests; see the build step."
  exit 0
fi

failed=$(grep -E '^//[^ ]+ +(FAILED|TIMEOUT|NO STATUS)' "$log" | awk '{print $1}')
if [ -z "$failed" ]; then
  echo "No failed test in the summary. See the test step for build errors."
  exit 0
fi

testlogs=$(bazel info bazel-testlogs 2>/dev/null)
for label in $failed; do
  echo "<details><summary><code>$label</code></summary>"
  echo
  rel="${label#//}"
  test_log="$testlogs/${rel/://}/test.log"
  echo '```'
  if [ -f "$test_log" ]; then tail -n 80 "$test_log"; else echo "No test.log at $test_log"; fi
  echo '```'
  echo "</details>"
done
