.PHONY: help setup install run dev dashboard chat chat-stream check test docker-up docker-down docker-logs clean

PY ?= python

help:         ## show all commands
	@grep -E "^[a-z-]+:.*##" Makefile | sed "s/:.*##/ -/"
setup:        ## copy .env.example to .env
	@test -f .env || cp .env.example .env && echo ".env ready - add your OPENAI_API_KEY"
install:      ## install dependencies
	$(PY) -m pip install -r requirements.txt
run:          ## start production server
	uvicorn api:app --host 0.0.0.0 --port 8000
dev:          ## start dev server with hot-reload
	uvicorn api:app --reload --port 8000
dashboard:    ## start server and print the dashboard URL
	@echo "Dashboard: http://localhost:8000/dashboard"
	uvicorn api:app --port 8000
chat:         ## ask agent from CLI: make chat Q="..."
	$(PY) agent.py -q "$(Q)"
chat-stream:  ## stream answer from CLI: make chat-stream Q="..."
	$(PY) agent.py --stream -q "$(Q)"
check:        ## validate config
	$(PY) config.py
test:         ## run tests
	$(PY) -m pytest -q
docker-up:    ## start with Docker Compose
	docker compose up -d --build
docker-down:  ## stop Docker Compose
	docker compose down
docker-logs:  ## tail container logs
	docker compose logs -f agent
clean:        ## remove logs & cache
	rm -rf *.log __pycache__ tests/__pycache__ .pytest_cache
