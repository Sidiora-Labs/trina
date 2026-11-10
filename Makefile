# Fleet gates (spec/workflow.kvx [hierarchy]): `make test` and `make lint`.
# --locked makes a gate fail when uv.lock no longer matches pyproject.toml.
UV_RUN ?= uv run --locked
PYTEST_ARGS ?=

.PHONY: test lint hygiene

# tests/conftest.py prints the "trina gate" line: skipped is counted apart from passed.
test:
	$(UV_RUN) pytest $(PYTEST_ARGS)

lint: hygiene
	$(UV_RUN) ruff check src tests scripts
	$(UV_RUN) mypy

hygiene:
	$(UV_RUN) python scripts/check_hygiene.py
