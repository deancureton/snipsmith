SHELL := /bin/bash

PYTHON := python3
VENV_DIR := .venv
VENV_PYTHON := $(VENV_DIR)/bin/python

# list of source files that should trigger a rebuild
SRC_FILES := build_snippets.py snippets.yaml .env

.PHONY: all snippets check clean help

all: snippets

# build the snippets; depends on the source files and the venv
snippets: $(VENV_DIR)/touchfile $(SRC_FILES)
	@echo ">>> Building snippets..."
	@$(VENV_PYTHON) build_snippets.py

# validate snippets.yaml without writing any files
check: $(VENV_DIR)/touchfile
	@echo ">>> Validating snippets..."
	@$(VENV_PYTHON) build_snippets.py --check

# set up the venv
$(VENV_DIR)/touchfile: requirements.txt
	@if [ ! -d "$(VENV_DIR)" ]; then \
		echo ">>> Creating virtual environment..."; \
		$(PYTHON) -m venv $(VENV_DIR); \
	fi
	@echo ">>> Installing/updating dependencies..."
	@$(VENV_PYTHON) -m pip install -r requirements.txt
	@touch $(VENV_DIR)/touchfile

# clean up snippet files and venv
clean:
	@echo ">>> Cleaning up..."
	@$(VENV_PYTHON) build_snippets.py --clean
	@rm -rf $(VENV_DIR)
	@find . -type f -name "*.pyc" -delete
	@find . -type d -name "__pycache__" -delete

# display available commands
help:
	@echo "Available commands:"
	@echo "  make snippets  - Install dependencies and build all snippet files."
	@echo "  make check     - Validate snippets.yaml without writing any files."
	@echo "  make clean     - Remove virtual environment and all generated files."
	@echo "  make help      - Show this help message."
