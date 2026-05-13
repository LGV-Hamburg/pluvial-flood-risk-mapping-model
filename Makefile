.ONESHELL:
SHELL=/bin/bash
.PHONY: build

config ?= .env

include $(config)
export $(shell sed 's/=.*//' $(config))

GIT_COMMIT := $(shell git rev-parse --short HEAD)

all: lock build-image run-local

lock:
	@echo 'Creating/Updating poetry lockfile'
	poetry lock


build-image: lock
	@echo 'Building release ${CONTAINER_REGISTRY}/$(IMAGE_NAME):$(IMAGE_TAG)'
	docker compose -f docker-compose-build.yaml build --build-arg SOURCE_COMMIT=$(GIT_COMMIT) app

push-registry:
	az acr login --name $(shell echo $(CONTAINER_REGISTRY) | cut -d. -f1)
	docker push ${CONTAINER_REGISTRY}/$(IMAGE_NAME):$(IMAGE_TAG)

run-local:
	docker compose -f docker-compose-dev.yaml up

test:
	poetry run pytest tests/

lint:
	poetry run ruff check src/

format:
	poetry run ruff format src/

typecheck:
	poetry run mypy src/

build-docs:
	jupyter-book build docs

clean-docs:
	jupyter-book clean docs
