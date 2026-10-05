# Inspired by: https://blog.mathieu-leplatre.info/tips-for-your-makefile-with-python.html
#             https://www.thapaliya.com/en/writings/well-documented-makefiles/

.DEFAULT_GOAL := help

help:  ## Display this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-25s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

.PHONY: install
install:  ## Install the package for development along with pre-commit hooks.
	uv sync --extra dev
	pre-commit install

.PHONY: test
test:  ## Run the tests with pytest.
	uv run pytest tests/ -q

.PHONY: pre-commit
pre-commit:  ## Run the pre-commit hooks on all files.
	pre-commit run --all-files --verbose

.PHONY: server
server:  ## Start the development server on port 8000.
	uv run boligregner --port 8000
