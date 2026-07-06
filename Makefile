.PHONY: install-hooks install-pre-commit test test-cov lint format typecheck hassfest-sanity clean help

help:  ## Show this help.
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install-hooks:  ## Install the standalone pre-commit hook into .git/hooks/pre-commit.
	@cp scripts/pre-commit .git/hooks/pre-commit
	@chmod +x .git/hooks/pre-commit
	@echo "✓ Installed scripts/pre-commit → .git/hooks/pre-commit"
	@echo "  Now runs on every 'git commit'. Bypass with --no-verify if needed."
	@echo "  (Or use the pre-commit framework: pip install pre-commit && pre-commit install)"

install-pre-commit:  ## Install pre-commit (the framework) and run pre-commit install.
	@uv tool install pre-commit || pip install --user pre-commit
	@pre-commit install
	@echo "✓ pre-commit framework installed and hooks wired."

test:  ## Run pytest.
	uv run pytest tests/ -q

test-cov:  ## Run pytest with coverage.
	uv run pytest tests/ -v --cov

lint:  ## Run ruff check + format check.
	uv run ruff check custom_components tests
	uv run ruff format --check custom_components tests

format:  ## Run ruff format (apply).
	uv run ruff format custom_components tests
	uv run ruff check --fix custom_components tests

typecheck:  ## Run mypy --strict.
	uv run mypy --strict custom_components

hassfest-sanity:  ## Run local hassfest-style sanity checks (no docker).
	uv run python scripts/hassfest_checks.py all

clean:  ## Remove caches and build artefacts.
	@find . -name __pycache__ -prune -exec rm -rf {} +
	@find . -name '.mypy_cache' -prune -exec rm -rf {} +
	@find . -name '.ruff_cache' -prune -exec rm -rf {} +
	@find . -name '.pytest_cache' -prune -exec rm -rf {} +
