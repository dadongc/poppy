.PHONY: test lint format check run migrate clean

test:
	pytest tests/unit -v

lint:
	ruff check src/ tests/

format:
	ruff format src/ tests/

check: lint
	mypy src/

run:
	uvicorn src.gateway.app:app --reload

migrate:
	python -m migrations.runner

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist/ *.egg-info/
	find . -type d -name __pycache__ -exec rm -rf {} +
