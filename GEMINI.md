# Workspace Instructions

This document provides instructions for the coding agent to work with the `registrarmonitor` project.

## Project Overview

`registrarmonitor` is a Python application for monitoring university registrar data. It downloads enrollment data, processes it, and generates reports in PDF and text format. It can also send notifications via Telegram.

## Tooling

This project uses the following tools:

*   **`uv`**: For package and virtual environment management.
*   **`ruff`**: For linting and formatting.
*   **`ty`**: For static type checking.

## Dependencies

This project uses `uv` for package management. `uv` will automatically create and manage a virtual environment. All dependencies are listed in `pyproject.toml`.

## Development Workflow

This project uses `ruff` for linting and formatting, and `ty` for type checking.

### Formatting

To format the code, run:
```bash
ruff format
```

### Linting

To check for linting errors, run:
```bash
ruff check
```

### Type Checking

To check for type errors, run:
```bash
ty check
```

## Committing

Before committing any changes, please ensure that you have:

1.  Formatted the code.
2.  Checked for linting errors.
3.  Checked for type errors.
4.  Run the tests.
5.  If the tests fail, fix the issues and repeat steps 2-4.

