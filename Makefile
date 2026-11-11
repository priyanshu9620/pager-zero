.PHONY: sync test lint fmt typecheck doctor demo-up demo-down demo-serve

sync:
	uv sync

test:
	MEMORY_BACKEND=memory LANGSMITH_TRACING=false uv run pytest -q -p no:warnings   # tests never trace

lint:
	uv run ruff check src tests

fmt:
	uv run ruff format src tests && uv run ruff check --fix src tests

typecheck:
	uv run mypy src

doctor:
	uv run pz doctor

# ---- demo stack: real containers, real faults (see demo/README.md) ----
DEMO_ENV = ADAPTER_BACKEND=live ACTUATOR=demo REDIS_URL=redis://localhost:6380/0 \
  LIVE_TOPOLOGY_FILE=demo/state/topology.json LIVE_DEPLOY_LOG=demo/state/deploys.json \
  LIVE_FLAG_FILE=demo/state/flags.json VERIFY_TIMEOUT_S=300

demo-up:
	uv run pz demo up

demo-down:
	uv run pz demo down

demo-serve:
	$(DEMO_ENV) uv run pz serve --host 0.0.0.0 --port 8000
