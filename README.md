# AI Agent — Production Ready

>  OpenAI GPT-4o  |  ReAct Reasoning  |  7 Built-in Tools  |  FastAPI REST  |  Docker  |  Deploy in Minutes

**Author:** Kirit Reddy Daida — [Portfolio](https://kirit-daida.netlify.app/) | [LinkedIn](https://www.linkedin.com/in/kirit-reddy-daida-a824b2353/)

---

## What This Is

A fully working, production-grade AI Agent you can run **right now, zero changes needed**.

- Powered by **OpenAI GPT-4o** with function-calling (tool use)
- Uses the **ReAct pattern** — thinks step-by-step, picks a tool, runs it, loops until it has the final answer
- Exposed as a **FastAPI REST API** with streaming support, Swagger docs, health checks, and optional auth
- Comes with **7 built-in tools**: calculator, web search, Python executor, data analyst, file reader, datetime, weather
- Deploy **locally in 3 minutes** or **with Docker in 1 command**

---

## Architecture

```
User Question
      |
      v
  FastAPI (api.py)
      |
      v
  AIAgent.run()  <── session memory
      |
      |── ReAct Loop ──────────────────────────────────────
      |   1. Call GPT-4o with tool schemas
      |   2. GPT returns tool_calls? → execute tools → append result → repeat
      |   3. GPT returns text?       → final answer → return
      |   4. max_iterations hit?     → safety fallback
      |────────────────────────────────────────────────────
      |
      v
  Tools (tools.py)
      calculator | web_search | python_executor
      data_analyst | file_reader | datetime_tool | weather
```

---

## File Structure

```
ai-agent-production-ready/
├── agent.py            # Core AI agent (ReAct loop, memory, streaming)
├── tools.py            # All tools + JSON schemas for OpenAI
├── api.py              # FastAPI REST server (+ dashboard & task endpoints)
├── monitor.py          # Live activity events + metrics
├── tasks.py            # Background work queue (SQLite-backed)
├── campaign.py         # LinkedIn autopilot: plans, writes, schedules & publishes posts
├── sources.py          # Reads your GitHub repos + Zenodo papers
├── linkedin_client.py  # LinkedIn OAuth + Posts API
├── static/dashboard.html  # Agent Control Room web dashboard
├── config.py           # Settings from environment variables
├── requirements.txt    # Python dependencies
├── .env.example        # Configuration template (copy to .env)
├── Dockerfile          # Production Docker image (multi-stage)
├── docker-compose.yml  # One-command Docker deployment
├── Makefile            # Developer shortcuts
├── tests/              # Offline test suite (fake OpenAI client, no API key needed)
└── README.md           # This file
```

---

## Quickstart — Local (3 Minutes)

### Step 1: Clone the Repo

```bash
git clone https://github.com/kiritreddy97-droid/ai-agent-production-ready.git
cd ai-agent-production-ready
```

### Step 2: Create Your Environment File

```bash
cp .env.example .env
```

Open `.env` and add your OpenAI API key:

```
OPENAI_API_KEY=sk-your-key-here
```

Get a free key at https://platform.openai.com/api-keys

### Step 3: Install Dependencies

```bash
pip install -r requirements.txt
# or using make:
make install
```

### Step 4: Verify Configuration

```bash
python config.py
# or:
make check
```

You should see all settings and "OpenAI Key: set".

### Step 5: Try the CLI

```bash
python agent.py -q "What is 42 * 17?"
python agent.py -q "Search the web for latest news about AI"
python agent.py --stream -q "Write and run Python code to generate a Fibonacci sequence"

# or using make:
make chat Q="What is the square root of 1764?"
make chat-stream Q="Search for the weather in Albuquerque NM"
```

### Step 6: Start the API Server

```bash
uvicorn api:app --reload --port 8000
# or:
make dev
```

Visit:
- **Dashboard (watch the agent, assign work):** http://localhost:8000/dashboard
- **Swagger UI (interactive docs):** http://localhost:8000/docs
- **Health check:** http://localhost:8000/health
- **Tool list:** http://localhost:8000/tools

---

## Dashboard — Agent Control Room

Open **http://localhost:8000/dashboard** while the server is running.

| Panel | What it shows |
|-------|---------------|
| **KPI tiles** | queued / running / completed / failed tasks, success rate, tokens, avg LLM latency, tool calls, uptime |
| **Assign work** | give the agent a task (prompt, optional title, priority, optional session to keep memory) — `Ctrl/⌘+Enter` submits |
| **Tasks** | every task with status, priority, duration and tokens; filter by status; click one for detail |
| **Task detail** | prompt, result or error, and a step-by-step **trace** (each LLM call, tool call with arguments, tool output, answer) — plus Retry / Run again / Cancel / Duplicate / Delete |
| **Workers** | each background worker and what it is working on right now |
| **Live activity** | stream of everything the agent does (runs, LLM calls, tool calls, answers, errors), from tasks *and* `/chat` requests; filter by Tools / Tasks / Errors; pause |
| **Tool usage** | how often each tool has been called and how often it failed |
| **Sessions** | conversation memory the agent is holding, with message and token counts |

It refreshes every 2 seconds, works in light and dark mode and on mobile, and needs no build step.
If `API_KEY` is set, click 🔑 and enter it (stored only in your browser).

**How work runs:** tasks go into a priority queue (high → normal → low) and `TASK_WORKERS`
background workers run them through the agent. Tasks are stored in SQLite (`TASKS_DB`), so they
survive restarts — queued tasks resume, and tasks interrupted mid-run are marked failed so you can retry them.

### Task API

```bash
# Assign work
curl -X POST http://localhost:8000/tasks -H "Content-Type: application/json" \
  -d '{"prompt": "Find all primes below 200 with Python", "priority": "high"}'

curl http://localhost:8000/tasks?status=done        # list (queued|running|done|failed|cancelled)
curl http://localhost:8000/tasks/<id>                # detail + full trace
curl -X POST http://localhost:8000/tasks/<id>/retry  # re-run failed/cancelled/done
curl -X POST http://localhost:8000/tasks/<id>/cancel # cancel while queued
curl http://localhost:8000/api/overview              # metrics, workers, sessions
curl "http://localhost:8000/api/events?after=0"      # activity feed (poll with ?after=<last_seq>)
```

> Live metrics and the activity feed are kept in memory per server process. Run with
> `API_WORKERS=1` (the default) so the dashboard sees everything; tasks themselves are safe
> with more processes (workers claim each task atomically).

---

## LinkedIn Autopilot — Post Your Work Every Monday & Thursday

The agent reads your **GitHub repos** and **Zenodo papers**, writes a LinkedIn post about one of
them, and publishes it on your schedule (default **Mon & Thu, 09:00**). Open the **LinkedIn** tab
in the dashboard to manage it.

**What gets posted** — new work first (alternating code and papers) → "what's new" posts when a
repo gets new commits → deep-dives on older work → a roundup every 6th post. Each post is written
only from the real README / commits / abstract (the prompt forbids invented numbers or claims),
with a hook, short paragraphs, a question to drive comments, and 3–5 hashtags. The repo/paper
link is attached as a preview card.

**Review mode (default)** — each post is drafted 48 h before its slot; you read, edit, then
**Approve** it and it publishes on time. Prefer hands-off? Flip **Mode → Autopilot** in the
dashboard (or `LINKEDIN_AUTO_PUBLISH=true`). You can also **Rewrite**, **Skip** (a different topic
is drafted for that slot), **Publish now**, or click **Draft post** on any repo/paper.

### Setup (≈10 minutes, once)

1. **Tell it where your work is** — in `.env`:
   ```
   GITHUB_USERNAME=kiritreddy97-droid
   ZENODO_ORCID=0000-0000-0000-0000      # best; or ZENODO_AUTHOR=Your Name
   LINKEDIN_TIMEZONE=America/New_York    # your time zone
   ```
2. **Create a LinkedIn app** at https://www.linkedin.com/developers/apps → *Create app*
   (LinkedIn asks you to link a Company Page — any page you admin works; you can create one).
3. In the app's **Products** tab, add **Share on LinkedIn** and
   **Sign In with LinkedIn using OpenID Connect** (both are instant).
4. In the **Auth** tab, add the redirect URL `http://localhost:8000/linkedin/callback`
   (or your server's URL + `/linkedin/callback`), and copy the Client ID / Secret into `.env`:
   ```
   LINKEDIN_CLIENT_ID=...
   LINKEDIN_CLIENT_SECRET=...
   ```
5. Restart the server, open **Dashboard → LinkedIn → Connect LinkedIn**, approve.
   Done — the first draft appears up to 48 h before the next Monday/Thursday slot
   (or click **Write next post now**).

### Things to know

- **The server must be running at posting time.** If your laptop is asleep at 09:00 Monday,
  nothing posts (a post more than 24 h late is never auto-published; you can publish it manually).
  For hands-off posting, deploy to an always-on host (a small VPS, Railway, or a paid Render
  instance — free tiers that sleep will miss slots) and set `LINKEDIN_REDIRECT_URI` to its URL.
- **LinkedIn connections last ~60 days.** The dashboard warns you a week before; click Reconnect.
- **Posts go out under your name.** Review mode is the default for that reason — the model is
  instructed to stick to facts from your repos and papers, but read before approving.
- LinkedIn's API version is chosen automatically; if LinkedIn ever rejects it, set
  `LINKEDIN_API_VERSION=YYYYMM` to a current version.

---

## API Usage

### Ask a question (curl)

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "What is 100 factorial?", "session_id": "demo"}'
```

**Response:**
```json
{
  "answer": "100! = 9.332622e+157...",
  "session_id": "demo",
  "model": "gpt-4o",
  "latency_ms": 1234
}
```

### Streaming response

```bash
curl -N -X POST http://localhost:8000/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"question": "Search for latest AI news", "session_id": "demo"}'
```

### Python client

```python
import requests

# Single response
resp = requests.post("http://localhost:8000/chat",
    json={"question": "Calculate sqrt(144) + 10", "session_id": "py-demo"})
print(resp.json()["answer"])

# Multi-turn conversation (same session_id keeps memory)
for q in ["My name is Kirit", "What is my name?"]:
    r = requests.post("http://localhost:8000/chat",
        json={"question": q, "session_id": "kirit-session"})
    print(f"Q: {q}")
    print(f"A: {r.json()['answer']}\n")
```

### JavaScript client (streaming)

```javascript
const response = await fetch("http://localhost:8000/chat/stream", {
  method: "POST",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify({question: "Hello!", session_id: "js-demo"})
});
const reader = response.body.getReader();
const decoder = new TextDecoder();
while (true) {
  const {value, done} = await reader.read();
  if (done) break;
  process.stdout.write(decoder.decode(value));
}
```

---

## Deployment — Docker (1 Command)

### Prerequisites
- Docker Desktop installed: https://www.docker.com/products/docker-desktop/
- `.env` file configured (see Step 2 above)

### Option A: Docker Compose (Recommended)

```bash
# Start the agent
docker compose up -d

# Check it is running
docker compose ps

# View live logs
docker compose logs -f agent

# Test the health endpoint
curl http://localhost:8000/health

# Stop
docker compose down
```

### Option B: Docker Run

```bash
# Build image
docker build -t ai-agent .

# Run with your .env file
docker run -d \
  -p 8000:8000 \
  --env-file .env \
  --name ai-agent \
  ai-agent

# Check logs
docker logs -f ai-agent
```

---

## Deployment — Cloud

### Deploy to Render (Free Tier Available)

1. Fork or push this repo to your GitHub
2. Go to https://render.com and click "New Web Service"
3. Connect your GitHub repo
4. Set these in Render settings:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn api:app --host 0.0.0.0 --port $PORT`
5. Add Environment Variables:
   - `OPENAI_API_KEY` = your key
   - `OPENAI_MODEL` = `gpt-4o`
6. Click "Create Web Service"
7. Your agent is live at `https://your-service.onrender.com`

### Deploy to Railway

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login and deploy
railway login
railway init
railway up

# Set env variables
railway variables set OPENAI_API_KEY=sk-your-key
railway variables set OPENAI_MODEL=gpt-4o
```

### Deploy to AWS / GCP / Azure

Use the Docker image — it runs on any container service:
- **AWS:** ECS Fargate, App Runner, or Elastic Beanstalk
- **GCP:** Cloud Run (`gcloud run deploy`)
- **Azure:** Container Instances or App Service

```bash
# Example: Google Cloud Run
gcloud run deploy ai-agent \
  --image gcr.io/your-project/ai-agent \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars OPENAI_API_KEY=sk-your-key
```

---

## Available Tools

| Tool | Description | Requires Key? |
|------|-------------|---------------|
| `calculator` | Safe math expressions (ast-based, no code injection) | No |
| `web_search` | DuckDuckGo search — internet info without API key | No |
| `python_executor` | Sandboxed Python code runner (no fs/network access) | No |
| `data_analyst` | Statistical summary for CSV/JSON datasets | No |
| `file_reader` | Read local .txt .csv .json .md .py .yaml files safely | No |
| `datetime_tool` | Current date, time, timezone info | No |
| `weather` | Current weather for any city | Yes (WEATHER_API_KEY) |

### Adding Your Own Tool

Open `tools.py` and add 3 things:

```python
# 1. The function
def my_tool(param: str) -> str:
    """One-line description shown to the AI."""
    return f"Result: {param}"

# 2. Register it
TOOL_REGISTRY["my_tool"] = my_tool

# 3. Add JSON schema (tells the AI how to call it)
TOOL_SCHEMAS.append({
    "type": "function",
    "function": {
        "name": "my_tool",
        "description": "One-line description for the AI",
        "parameters": {
            "type": "object",
            "properties": {"param": {"type": "string"}},
            "required": ["param"]
        }
    }
})
```

Done! The agent will automatically discover and use your tool.

---

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | **(required)** | Your OpenAI API key |
| `OPENAI_MODEL` | `gpt-4o` | Model name. Also works: `gpt-4-turbo`, `gpt-3.5-turbo` |
| `MAX_TOKENS` | `4096` | Max tokens per response |
| `TEMPERATURE` | `0.2` | 0=deterministic, 1=creative |
| `MAX_ITERATIONS` | `10` | Max ReAct reasoning loops (safety guard) |
| `MAX_RETRIES` | `3` | Retries on API errors |
| `API_HOST` | `0.0.0.0` | Server host |
| `API_PORT` | `8000` | Server port |
| `API_KEY` | *(empty)* | Optional Bearer token auth |
| `WEATHER_API_KEY` | *(empty)* | OpenWeatherMap key (free at openweathermap.org) |
| `LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING / ERROR |
| `TASK_WORKERS` | `2` | Background workers that run assigned tasks |
| `TASKS_DB` | `tasks.db` | SQLite file storing tasks and their traces |
| `EVENT_BUFFER` | `500` | Recent events kept for the live activity feed |
| `GITHUB_USERNAME` | *(empty)* | GitHub account whose public repos are posted about |
| `GITHUB_TOKEN` | *(empty)* | Optional, raises GitHub API rate limit |
| `GITHUB_EXCLUDE` | *(empty)* | Comma-separated repo names never to post about |
| `ZENODO_ORCID` / `ZENODO_AUTHOR` | *(empty)* | Finds your Zenodo publications |
| `LINKEDIN_CLIENT_ID` / `LINKEDIN_CLIENT_SECRET` | *(empty)* | Your LinkedIn developer app |
| `LINKEDIN_REDIRECT_URI` | `http://localhost:8000/linkedin/callback` | Must match the app's Auth settings |
| `LINKEDIN_POST_DAYS` | `mon,thu` | Posting days |
| `LINKEDIN_POST_TIME` | `09:00` | Posting time (24 h) |
| `LINKEDIN_TIMEZONE` | `UTC` | IANA zone, e.g. `America/New_York` |
| `LINKEDIN_AUTO_PUBLISH` | `false` | `true` = publish without review |
| `LINKEDIN_DRAFT_AHEAD_HOURS` | `48` | How early each post is drafted |
| `REDIS_URL` | *(empty)* | Redis for persistent sessions (optional) |

---

## Make Commands

```bash
make help          # show all commands
make setup         # copy .env.example to .env
make install       # install dependencies
make run           # start production server
make dev           # start dev server with hot-reload
make dashboard     # start server and print the dashboard URL
make chat Q="..."  # ask agent from CLI
make check         # validate config
make test          # run tests
make docker-up     # start with Docker Compose
make docker-down   # stop Docker Compose
make docker-logs   # tail container logs
make clean         # remove logs & cache
```

---

## Troubleshooting

**"OPENAI_API_KEY is not set"**
  Run `cp .env.example .env` and add your key.

**"Module not found"**
  Run `pip install -r requirements.txt`

**"Connection refused" on port 8000**
  Make sure the server is running: `uvicorn api:app --port 8000`

**"RateLimitError" from OpenAI**
  You have hit your API rate limit. Wait a minute or upgrade your OpenAI plan.

**Agent gives wrong answer**
  Lower `TEMPERATURE` to `0.0` for more deterministic responses.
  Increase `MAX_ITERATIONS` if the agent needs more tool calls.

---

## Built With

- [OpenAI Python SDK](https://github.com/openai/openai-python)
- [FastAPI](https://fastapi.tiangolo.com/)
- [Uvicorn](https://www.uvicorn.org/)
- [Pydantic](https://docs.pydantic.dev/)
- [DuckDuckGo](https://duckduckgo.com/) (free web search)

---

## Author

**Kirit Reddy Daida** — Training Evaluator II | Data & AI Analyst

- Portfolio: https://kirit-daida.netlify.app/
- LinkedIn: https://www.linkedin.com/in/kirit-reddy-daida-a824b2353/
- Email: kiritreddy97@gmail.com

---

*Built as part of a production-ready project portfolio demonstrating full-stack AI engineering.*# ai-agent-production-ready
Production-ready AI Agent with OpenAI GPT-4, ReAct reasoning, multi-tool use, memory, REST API — deploy locally or with Docker in minutes. Zero changes needed.
