uv := `which uv`

export PY_COLORS := "1"
export PYTHONBREAKPOINT := "pdb.set_trace"

uv_run := "uv run --frozen --extra dev"

# Regenerate uv.lock.
lock:
    uv lock

# Create a development environment.
env: lock
    uv sync --extra dev

# Upgrade uv.lock with the latest deps
upgrade:
    uv lock --upgrade

repo *args: lock
    {{uv_run}} repository.py {{args}}