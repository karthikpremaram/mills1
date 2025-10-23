# MILLIS1 — Short README


Quick setup
-----------
1. Provide required environment variables (see `src/core/config.py`). Common ones:
   - OPENAI_API_KEY, GEMINI_API_KEY, MILLIS_API_KEY, OPENAI_MODEL_NAME, REDIS_URL
2. Install dependencies and start Redis (or point `REDIS_URL` to a running instance).
3. Run the API and worker (set `PYTHONPATH=src` when running locally):

```bash
# Run API (example)
uvicorn app:app --reload --host 0.0.0.0 --port 8000

# Run ARQ worker (example)
arq src.core.arq_worker.WorkerSettings
```

Key endpoints
-------------
- POST /agents — enqueue assistant creation (payload: {main_url, assistant_name, idempotency_key})
- GET /tasks/{task_id} — fetch task state and logs
- DELETE /tasks/{task_id} — cancel a task

Important files
---------------
- `app.py`, `app_2.py` — API entry points
- `src/core/arq_worker.py` — ARQ worker settings
- `src/jobs/tasks.py` — orchestration job
- `src/agent.py` — agent-driven prompt creation
- `src/scrape/` — scraping & cleaning
