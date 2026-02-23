.PHONY: install install-dev run test lint clean

install:
	pip install -e .

install-dev:
	pip install -e ".[dev]"

run:
	centinela chat

serve:
	centinela serve

test:
	pytest -v

lint:
	ruff check src/ tests/
	ruff format --check src/ tests/

format:
	ruff format src/ tests/

clean:
	rm -rf build/ dist/ *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
