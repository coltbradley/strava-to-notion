# Development Guide

## Running Tests

Install test dependencies:
```bash
pip install pytest pytest-cov
```

Run tests:
```bash
pytest tests/
```

Run tests with coverage:
```bash
pytest --cov=sync --cov-report=term-missing tests/
```

## Linting and Formatting

Install ruff:
```bash
pip install ruff
```

Run linter:
```bash
ruff check sync.py
```

Format code:
```bash
ruff format sync.py
```

## Type Checking (Optional)

Install mypy:
```bash
pip install mypy
```

Run type checker:
```bash
mypy sync.py
```

## Code Quality Checks

Before committing, run:
```bash
ruff check sync.py && ruff format --check sync.py && pytest tests/
```

