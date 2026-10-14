.PHONY: help setup install run dev chat chat-stream check test docker-up docker-down docker-logs clean

help:
	@grep -E '^[a-z-]+:' Makefile | cut -d: -f1 | sort

setup:
	@test -f .env || cp .env.example .env

install:
	pip install -r requirements.txt

run:
	uvicorn api:app --host 0.0.0.0 --port 8000

dev:
	uvicorn api:app --reload --port 8000

chat:
	python agent.py -q "$(Q)"

chat-stream:
	python agent.py --stream -q "$(Q)"

check:
	python config.py

test:
	python -m pytest -q

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f agent

clean:
	rm -rf __pycache__ .pytest_cache *.log
