.PHONY: sync test lint fmt typecheck doctor

sync:
	uv sync

test:
	MEMORY_BACKEND=memory uv run pytest -q -p no:warnings

lint:
	uv run ruff check src tests

fmt:
	uv run ruff format src tests && uv run ruff check --fix src tests

typecheck:
	uv run mypy src

doctor:
	uv run pz doctor
