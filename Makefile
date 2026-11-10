# Fleet gates (spec/workflow.kvx [hierarchy]): `make test` and `make lint`.
# --locked makes a gate fail when uv.lock no longer matches pyproject.toml.
UV_RUN ?= uv run --locked
PYTEST_ARGS ?=

.PHONY: test lint hygiene artifacts

# tests/conftest.py prints the "trina gate" line: skipped is counted apart from passed.
test:
	$(UV_RUN) pytest $(PYTEST_ARGS)

lint: hygiene artifacts
	$(UV_RUN) ruff check src tests scripts
	$(UV_RUN) mypy

hygiene:
	$(UV_RUN) python scripts/check_hygiene.py

# Artifact scan (clause 3.1): credential patterns and loaded secret values in data/ and runs/.
artifacts:
	$(UV_RUN) python -m trina.security
