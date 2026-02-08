export WEBCTL_ADBLOCK_ENABLED=0
uv run webctl start
uv run webctl navigate https://www.spiegel.de
uv run webctl snapshot
echo "To Stop:"
echo uv run webctl stop