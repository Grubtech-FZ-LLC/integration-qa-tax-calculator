.PHONY: help install install-dev test test-cov lint format clean build publish

help: ## Show this help message
	@echo "Integration QA Tax Calculator - Development Commands"
	@echo "===================================================="
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install production dependencies
	pip install -r requirements.txt

install-dev: ## Install development dependencies
	pip install -e ".[dev]"

test: ## Run tests (no tests directory currently)
	@echo "No tests directory found"

test-cov: ## Run tests with coverage (no tests directory currently)
	@echo "No tests directory found"

test-watch: ## Run tests in watch mode (no tests directory currently)
	@echo "No tests directory found"

lint: ## Run linting checks
	flake8 src/
	mypy src/

format: ## Format code
	black src/
	isort src/

format-check: ## Check code formatting
	black --check src/
	isort --check-only src/

security: ## Run security checks
	bandit -r src/
	safety check

pre-commit: ## Run pre-commit hooks
	pre-commit run --all-files

clean: ## Clean build artifacts
	@echo Cleaning build artifacts
	@python -c "import shutil,glob,os; [shutil.rmtree(p, ignore_errors=True) for p in ['build','dist','htmlcov'] if True]; [shutil.rmtree(p, ignore_errors=True) for p in glob.glob('*.egg-info')]; [os.remove(f) for f in ['.coverage'] if os.path.exists(f)]"
	@python - <<PY
import os
for root, dirs, files in os.walk('.', topdown=False):
	for d in list(dirs):
		if d == '__pycache__':
			try:
				import shutil; shutil.rmtree(os.path.join(root, d), ignore_errors=True)
			except Exception: pass
	for f in list(files):
		if f.endswith('.pyc'):
			try:
				os.remove(os.path.join(root, f))
			except Exception: pass
PY

build: ## Build package
	python -m build

publish: ## Publish to PyPI (requires authentication)
	twine upload dist/*

check-package: ## Check built package
	twine check dist/*

setup-hooks: ## Install pre-commit hooks
	pre-commit install

update-hooks: ## Update pre-commit hooks
	pre-commit autoupdate

docs: ## Build documentation
	cd docs && make html

docs-serve: ## Serve documentation locally
	cd docs/_build/html && python -m http.server 8000

venv: ## Create virtual environment
	python -m venv venv
	@echo "Virtual environment created. Activate it with:"
	@echo "  source venv/bin/activate  # Linux/macOS"
	@echo "  venv\\Scripts\\activate     # Windows"

setup: venv install-dev setup-hooks ## Complete setup for development
	@echo "Development environment setup complete!"

ci: lint test-cov security ## Run CI checks locally
	@echo "All CI checks passed!"

