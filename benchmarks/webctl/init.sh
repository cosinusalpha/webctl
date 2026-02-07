# Setting up python venv
uv venv
# Installing webctl package in editable mode
uv pip install -e ../../
# Setup webctl
uv run webctl setup
# setup skill, goose will install it in .agents
uv run webctl init -a goose
# Rename

