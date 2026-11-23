# Safe Web Interactive Agent — Backend

> **PS-11 Hackathon** | Python 3.11+ | FastAPI | Playwright | OpenRouter

An autonomous AI backend that lets students give natural-language instructions to navigate their college portal — checking electives, downloading hall tickets, registering electives, checking fees, and submitting leave — all with **prompt-injection protection** and **official organizer confirmation** before every irreversible action.

---

## Architecture

```
React Frontend
     │  REST / JSON
     ▼
FastAPI Backend (main.py)
  ├── routes/task_routes.py      ← Start runs, get status
  ├── routes/agent_routes.py     ← Execute agent, poll logs
  ├── routes/approval_routes.py  ← Frontend approval UI (supplementary)
  └── routes/report_routes.py    ← Get & submit reports
        │
        ▼
  services/task_manager.py       ← Orchestrates full workflow
    ├── portal_service.py        ← Organizer portal API client
    ├── llm_service.py           ← OpenRouter → structured action plan
    ├── browser_service.py       ← Playwright Chromium automation
    ├── security_service.py      ← Prompt-injection detection
    ├── approval_service.py      ← Official confirm.py gate
    └── report_service.py        ← Final report submission
        │
        ▼
  utils/action_logger.py         ← JSONL action log per run
  utils/security_rules.py        ← Deterministic regex rules (9 rule sets)
```

---

## Folder Structure

```
backend/
├── main.py               FastAPI app + CORS + routers + health
├── config.py             All env vars via python-dotenv
├── requirements.txt
├── .env.example
│
├── routes/
│   ├── task_routes.py
│   ├── agent_routes.py
│   ├── approval_routes.py
│   └── report_routes.py
│
├── services/
│   ├── portal_service.py
│   ├── llm_service.py
│   ├── browser_service.py
│   ├── security_service.py
│   ├── approval_service.py
│   ├── report_service.py
│   └── task_manager.py
│
├── schemas/
│   ├── task_schema.py
│   ├── agent_schema.py
│   ├── approval_schema.py
│   └── report_schema.py
│
├── utils/
│   ├── action_logger.py    JSONL logger (no passwords ever logged)
│   └── security_rules.py   9 deterministic injection rule sets
│
├── logs/                   Per-run JSONL action logs
├── tests/
│   ├── test_health.py
│   ├── test_security.py
│   └── test_logger.py
└── confirm.py              ← Place official organizer file here
```

---

## Installation

### 1. Prerequisites

- Python 3.11+
- pip

### 2. Create virtual environment

```powershell
cd "e:\Automated AI Agent for Web Access\backend"
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Install Playwright browser

```powershell
playwright install chromium
```

### 4. Configure environment

```powershell
copy .env.example .env
# Then edit .env with your values
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `PORTAL_URL` | ✅ | Organizer portal base URL (no trailing slash) |
| `TEAM_CODE` | ✅ | Your hackathon team code (sent in `X-Team-Code` header) |
| `OPENROUTER_API_KEY` | ✅ | OpenRouter API key |
| `OPENROUTER_MODEL` | ✅ | Model name, e.g. `openai/gpt-4o-mini` |
| `FRONTEND_URL` | ✅ | React dev server URL for CORS |
| `LOG_DIRECTORY` | — | Path for JSONL logs (default: `logs/`) |
| `BROWSER_HEADLESS` | — | `true` / `false` (default: `true`) |
| `BROWSER_TIMEOUT_MS` | — | Playwright timeout in ms (default: `30000`) |
| `HTTP_TIMEOUT` | — | Portal HTTP timeout seconds (default: `30`) |
| `DEBUG` | — | Enable verbose logging (default: `false`) |

---

## Running the Backend

```powershell
cd "e:\Automated AI Agent for Web Access\backend"
.\venv\Scripts\Activate.ps1

# Development (auto-reload)
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Or directly
python main.py
```

Swagger UI → http://localhost:8000/docs  
ReDoc → http://localhost:8000/redoc  
Health → http://localhost:8000/health

---

## Organizer Portal Integration

The backend follows the official PS-11 API contract exactly:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/tasks` | List practice tasks |
| `POST` | `/api/runs` | Start a new run |
| `GET` | `/api/run/{run_id}/request` | Get task details & credentials |
| `POST` | `/api/run/{run_id}/actions` | Submit action log |
| `POST` | `/api/run/{run_id}/report` | Submit final report |

All requests include `X-Team-Code: {TEAM_CODE}` header.

### Official confirm.py

Place the organizer-supplied `confirm.py` in the `backend/` directory (or project root). The `ApprovalService` auto-discovers it at startup.

```python
# confirm.py signature (organizer-provided)
def confirm(action_description: str, base_url: str, run_id: str) -> bool:
    ...
```

> ⚠️ Without `confirm.py`, all irreversible actions are **blocked** for safety.

---

## End-to-End Workflow

```
1. POST /api/task/start          {"task_id": "p01"}
   → runs portal, gets run_id & task request

2. POST /api/agent/execute       {"run_id": "..."}
   → LLM parses request → structured plan
   → Playwright logs in, navigates portal
   → Security scans every page
   → Reversible actions: execute
   → Irreversible actions: call confirm() → execute only if True

3. GET  /api/agent/status/{run_id}   (poll until done/not_done)

4. GET  /api/agent/logs/{run_id}     → JSONL action history

5. POST /api/report/{run_id}/submit  → sends log + report to portal
```

---

## Security Design

### Prompt Injection Protection (9 rule sets)

| Rule ID | Threat |
|---|---|
| INJ-001 | Ignore-instructions attack |
| INJ-002 | Credential disclosure |
| INJ-003 | External data exfiltration |
| INJ-004 | Task hijacking |
| INJ-005 | Unauthorized payment |
| INJ-006 | Role escalation |
| INJ-007 | Jailbreak / DAN |
| INJ-008 | Fake system notice |
| INJ-009 | Unauthorized elective drop |

**Architecture rule:** Deterministic regex rules always run first. An LLM classifier can supplement but never replace them.

### Credential Safety
- Passwords / OTPs are **never** passed to the LLM
- Passwords are **never** written to action logs
- `sanitize_value()` in logger provides a last-resort guard

### Confirmation Gate
- Every irreversible action requires a **fresh** `confirm()` call
- One approval never covers multiple actions
- Frontend approval is recorded but cannot bypass `confirm.py`

---

## OpenRouter Configuration

```
OPENROUTER_MODEL=openai/gpt-4o-mini    # cheapest, fast
OPENROUTER_MODEL=anthropic/claude-3-haiku   # alternative
OPENROUTER_MODEL=openai/gpt-4o        # most capable
```

Browse models at https://openrouter.ai/models

---

## API Endpoints

### Tasks
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/task/start` | Start organizer run |
| `GET` | `/api/task/{run_id}` | Run status |
| `GET` | `/api/task` | Available practice tasks |

### Agent
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/agent/execute` | Start workflow (async) |
| `GET` | `/api/agent/status/{run_id}` | Execution status |
| `GET` | `/api/agent/logs/{run_id}` | Action log |

### Approval
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/approval/respond` | Student UI response |

### Reports
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/report/{run_id}` | View report |
| `POST` | `/api/report/{run_id}/submit` | Submit to portal |

---

## Testing

```powershell
# From backend directory, with venv active
pytest tests/ -v

# Single test file
pytest tests/test_security.py -v

# With coverage
pip install pytest-cov
pytest tests/ --cov=. --cov-report=term-missing
```

> Tests use mocked portal responses. No real irreversible operations are performed during testing.

---

## Playwright Setup Notes

```powershell
# Install only Chromium (smallest)
playwright install chromium

# Install with system deps (Linux)
playwright install --with-deps chromium
```

The browser runs **headless** by default (`BROWSER_HEADLESS=true`). Set to `false` for local debugging to watch the browser navigate live.

