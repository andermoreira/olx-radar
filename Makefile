PY ?= python3
VENV = .venv
BIN = $(VENV)/bin

.PHONY: venv scan publish-local test lint clean

venv:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -q -r requirements.txt -r requirements-dev.txt

scan:
	$(BIN)/python -m radar scan --out results.json

# Dispara o workflow de publicação no GitHub com o results.json local.
# Requer o gh CLI autenticado (gh auth login).
publish-local: scan
	gh workflow run "OLX Radar publish" -f results="$$(cat results.json)"

test:
	$(BIN)/pytest -q

lint:
	$(BIN)/ruff check radar tests

clean:
	rm -rf $(VENV) results.json .pytest_cache .ruff_cache
