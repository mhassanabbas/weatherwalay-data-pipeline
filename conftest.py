# This file's presence tells pytest to add the repository root to
# Python's import path. Without it, GitHub Actions can't find api.py
# when running `pytest tests/` (works fine locally with `python -m pytest`,
# but not with plain `pytest` in CI).
