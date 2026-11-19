#!/usr/bin/env bash
set -eu
if [ -z "${SEC_USER_AGENT:-}" ]; then
    echo "Set SEC_USER_AGENT as a Codespaces secret for this repository, then stop and restart the codespace."
    exit 1
fi
if /opt/venv/bin/python -c 'from urllib.request import urlopen; import json; assert json.load(urlopen("http://127.0.0.1:8053/api/companies", timeout=2)).get("live_only")' 2>/dev/null; then
    exit 0
fi
umask 077
nohup /opt/venv/bin/python -m equity_research serve-research --root /app --live-only --port 8053 \
    > /tmp/equity-demo.log 2>&1 </dev/null &
pid=$!
for attempt in {1..30}; do
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "The builder stopped during startup. See /tmp/equity-demo.log."
        exit 1
    fi
    if /opt/venv/bin/python -c 'from urllib.request import urlopen; urlopen("http://127.0.0.1:8053/api/companies", timeout=2).close()' 2>/dev/null; then
        echo "Builder ready. Open private port 8053 in the Ports tab."
        exit 0
    fi
    sleep 1
done
echo "Timed out waiting for the builder. See /tmp/equity-demo.log."
exit 1
