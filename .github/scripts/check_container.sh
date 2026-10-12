#!/usr/bin/env bash
# Check the non-root runtime, writable data directory, imports and Berlin time.
set -euo pipefail

docker run --rm -i --entrypoint sh "$1" -c \
  'test "$(id -u)" != 0 && touch data/probe && python -' <<'PY'
import os
import time
from datetime import UTC, datetime
import run_finder, job_finder.review
assert os.environ["TZ"] == "Europe/Berlin"
for month, hour in ((1, 1), (7, 2)):
    assert time.localtime(datetime(2026, month, 15, tzinfo=UTC).timestamp()).tm_hour == hour
PY
