#!/usr/bin/env bash
# Run a command in CI; if it fails, publish the end of its output (and
# any ERROR lines) as a GitHub annotation so the cause is visible on the
# run summary page without opening the step log.
#
#   scripts/ci-run.sh <label> <command> [args...]
set -uo pipefail

label="$1"
shift
log="$(mktemp)"

"$@" 2>&1 | tee "$log"
status="${PIPESTATUS[0]}"

if [ "$status" -ne 0 ]; then
    {
        echo "$label failed with exit code $status"
        if grep -q "ERROR" "$log"; then
            echo "--- ERROR lines ---"
            grep "ERROR" "$log" | tail -n 25
        fi
        echo "--- last 40 lines ---"
        tail -n 40 "$log"
    } | cut -c1-400 > "$log.msg"
    # Annotation text: escape %, CR and LF as GitHub's workflow commands require.
    msg="$(sed -e 's/%/%25/g' -e 's/\r/%0D/g' "$log.msg" | sed -e ':a' -e 'N' -e '$!ba' -e 's/\n/%0A/g')"
    echo "::error title=$label::$msg"
fi
exit "$status"
