#!/bin/bash
set -e

uv run webctl start --mode unattended
uv run webctl navigate https://www.spiegel.de
uv run webctl snapshot --view md
uv run webctl navigate https://www.amazon.de
uv run webctl snapshot --view md
echo "To Stop:"
echo uv run webctl stop
