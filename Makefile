# Makefile for Nyanpass Panel

# Variables
PYTHON := python3
PIP := pip3
SRC_DIR := src
PACKAGE_DIR := $(SRC_DIR)/nyanpass_panel
MAIN_FILE := $(SRC_DIR)/main.py
REQUIREMENTS := $(SRC_DIR)/requirements.txt
TEST_DIR := tests
BUILD_DIR := build
DIST_DIR := dist

# Docker variables
DOCKER := docker
DOCKER_COMPOSE := docker compose
IMAGE_NAME := nyanpass-panel
CONTAINER_NAME := nyanpass-panel-app

# Default target
.PHONY: all
all: install

# Install dependencies
.PHONY: install
install:
	$(PIP) install -r $(REQUIREMENTS)

# Run the application
.PHONY: run
run:
	$(PYTHON) $(MAIN_FILE)

# Run development server
.PHONY: dev
dev:
	$(PYTHON) $(MAIN_FILE)

# Install package in development mode
.PHONY: develop
develop:
	$(PIP) install -e .

# Run tests
.PHONY: test
test:
	$(PYTHON) -m pytest $(TEST_DIR)

# Clean build artifacts
.PHONY: clean
clean:
	rm -rf $(BUILD_DIR) $(DIST_DIR) *.egg-info

# Create distribution packages
.PHONY: dist
dist: clean
	$(PYTHON) setup.py sdist bdist_wheel

# Install from distribution
.PHONY: install-dist
install-dist: dist
	$(PIP) install dist/*.whl

# Lint the code
.PHONY: lint
lint:
	$(PYTHON) -m flake8 $(SRC_DIR) $(TEST_DIR)
	black --check $(SRC_DIR) $(TEST_DIR)

# Format the code
.PHONY: format
format:
	black $(SRC_DIR) $(TEST_DIR)

# Check for security issues
.PHONY: security
security:
	bandit -r $(SRC_DIR)

# Build Docker image
.PHONY: docker-build
docker-build:
	$(DOCKER) build -t $(IMAGE_NAME) .

# Run Docker container
.PHONY: docker-run
docker-run:
	$(DOCKER) run --rm -p 5000:5000 --name $(CONTAINER_NAME) $(IMAGE_NAME)

# Build and run Docker container
.PHONY: docker-up
docker-up: docker-build docker-run

# Build with docker-compose
.PHONY: compose-build
compose-build:
	$(DOCKER_COMPOSE) build

# Run with docker-compose
.PHONY: compose-up
compose-up:
	$(DOCKER_COMPOSE) up -d

# Stop docker-compose services
.PHONY: compose-down
compose-down:
	$(DOCKER_COMPOSE) down

# View docker-compose logs
.PHONY: compose-logs
compose-logs:
	$(DOCKER_COMPOSE) logs -f

# Help target
.PHONY: help
help:
	@echo "Nyanpass Panel Makefile"
	@echo "======================"
	@echo "Available targets:"
	@echo "  all                  Install dependencies (default)"
	@echo "  install              Install dependencies"
	@echo "  run                  Run the application"
	@echo "  dev                  Run development server"
	@echo "  develop              Install package in development mode"
	@echo "  test                 Run tests"
	@echo "  clean                Clean build artifacts"
	@echo "  dist                 Create distribution packages"
	@echo "  install-dist         Install from distribution"
	@echo "  lint                 Lint the code"
	@echo "  format               Format the code"
	@echo "  security             Check for security issues"
	@echo ""
	@echo "Docker targets:"
	@echo "  docker-build         Build Docker image"
	@echo "  docker-run           Run Docker container"
	@echo "  docker-up            Build and run Docker container"
	@echo "  compose-build        Build with docker-compose"
	@echo "  compose-up           Run with docker-compose"
	@echo "  compose-down         Stop docker-compose services"
	@echo "  compose-logs         View docker-compose logs"
	@echo ""
	@echo "  help                 Show this help message"