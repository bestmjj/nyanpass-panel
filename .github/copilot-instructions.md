# Copilot Instructions for Nyanpass Panel

## Project Overview

**Nyanpass Panel** is a Python/Flask-based web dashboard for managing Nyanpass VPN subscriptions and automated network infrastructure tasks. It aggregates user subscription data, forward rules, and automates Cloudflare DNS updates via scheduled jobs.

### Architecture Summary

- **Backend**: Flask web app (`src/app.py`) with APScheduler-based job runner
- **Frontend**: Single-page HTML/vanilla JS (`src/static/index.html`) for UI
- **Configuration**: JSON-based (`data/config.json`) storing auth, jobs, and cached data
- **Deployment**: Docker containerized (Alpine Linux + Python 3.11)

### Core Components

1. **Job Execution Engine** (`run_job` function in `app.py`)
   - Authenticates with Nyanpass API using job credentials
   - Fetches user info, forward rules, device groups, traffic statistics
   - Synchronizes Cloudflare DNS records if configured
   - Sends Telegram notifications on DNS updates
   - Persists results to `config.json` for UI display

2. **Scheduler** (`BackgroundScheduler` via APScheduler)
   - Interval-based job scheduling (configurable per job in minutes)
   - Timezone-aware using `pytz` (defaults to "Asia/Shanghai")
   - In-memory job store; jobs persist in JSON config
   - Thread pool executor with 10 worker threads

3. **Session Management**
   - Flask sessions with 30-minute expiry
   - Shared auth credentials stored in `config.json` (not per-user)
   - `@require_auth` decorator protects API routes

4. **API Routes**
   - `/login` (POST) - credentials validated against config
   - `/logout` - clears session
   - `/api/config` (GET/POST) - manages jobs and auth settings
   - `/api/run/<job_id>` (POST) - triggers immediate job execution (async)
   - `/api/domains/<job_id>/<rule_id>` (GET/POST/DELETE) - manages domain mappings for forwarding rules
   - `/` - serves `index.html` (protected by auth)

## Critical Patterns & Conventions

### Job Structure in config.json

Each job entry in `config.json["jobs"]` must include:
```json
{
  "job_1762528743635": {
    "enabled": true,
    "interval_minutes": 15,
    "username": "...",
    "password": "...",
    "nya_host": "https://nya.trp.sh",
    "cf_token": "...",
    "domain": "example.com",
    "telegram_bot_token": "...",
    "telegram_chat_id": "...",
    "user_info": "...",
    "forward_rules": [...],
    "device_groups": [...],
    "last_log": "...",
    "last_run": "ISO timestamp"
  }
}
```

Key field behaviors:
- **Credentials**: `password`, `cf_token`, `telegram_bot_token` masked as `"********"` in API responses
- **API credentials** restored from original job on config update
- **Results**: `user_info`, `forward_rules`, `device_groups`, `last_log` written by `run_job`
- **Job ID**: UUID-like string (recommend `int(time.time() * 1000)` pattern)

### Nyanpass API Integration
2. `GET /api/v1/user/devicegroup` → device group list
3. `GET /api/v1/user/info` → user subscription details (expiry, traffic, plan)
All requests include `Authorization: <token>` header. 30-second timeout.

Flow in `run_job`:
1. Extract `connect_host` from device group ID=1 (IEPL inbound)
2. Query Cloudflare Zone API using `cf_token` (Bearer auth)
3. Find/update DNS record matching `domain` parameter
4. If IP changed: update via `PUT /zones/{zone_id}/dns_records/{record_id}` with TTL=120
5. On success: send Telegram notification

Parse device group fields:
- `id` - unique identifier
- `connect_host` - IP/hostname (usually `id == 1` holds the CT/CM IP)
- `name`, `note` - display info

### Logging Pattern

Every message in `run_job` prefixes with timezone-aware `[YYYY-MM-DD HH:MM:SS]` and prints to `stderr` (forced by `sys.stdout = sys.stderr` at module load). Logs persist in job config under `last_log`.

Example:
```
[2025-12-01 10:30:45] 登录成功
[2025-12-01 10:30:46] 流量统计: 今日流量: 1.23 GiB | 昨日流量: 2.45 GiB
```

### UI Patterns

- All config updates fetch fresh data via `/api/config` (GET), display editable form, POST changes
- Sensitive fields (password, tokens) replaced with `"********"` on display; original retained if unchanged on POST
 **Multi-IP support**: Extend from single CT IP to support CM (China Mobile) and other operators
 **Domain validation**: Add regex validation before DNS update to prevent invalid domains
### Local Testing

```bash
# Install dependencies
pip install -r src/requirements.txt

# Run Flask app (auto-loads config.json)
python src/app.py
# Opens on http://localhost:5000
```

### Docker Workflow

```bash
# Build & run with docker-compose
docker-compose up --build

# Container starts Flask on port 5000, binds config.json volume
# Logs: docker-compose logs -f nyanpass_viewer
```

### Config Initialization

- First run: checks for `config.json` in working directory
- If missing: creates with default `username=admin`, `password=change_this_password`
- Always applied from current working directory (or mounted path in Docker)

## Key Files & Responsibilities

| File | Purpose |
|------|---------|
| `src/app.py` | Core Flask app, job logic, API routes, scheduler |
| `src/static/index.html` | SPA dashboard — config form, logs viewer, manual run UI |
| `src/requirements.txt` | Dependencies: flask, pytz, APScheduler, flask_httpauth (unused) |
| `data/config.json` | Persistent storage for auth, jobs, cached results |
| `Dockerfile` | Alpine Linux + Python 3.11 build, CMD runs `python3 app.py` |
| `docker-compose.yml` | Service def with port 5000 → 5000, config.json volume |

## Common Pitfalls & Notes

1. **Timezone**: Defaults to "Asia/Shanghai" — changing in config restarts scheduler
2. **Session Expiry**: 30 minutes; no auto-refresh on idle (manual re-login required)
3. **Job Persistence**: Job state only saved after successful/failed run; unsaved edits in UI require POST
4. **API Timeouts**: 30 seconds for all Nyanpass/Cloudflare calls; silent failure logged in job log
5. **Device Group ID=1**: Assumed to be IEPL inbound group for CT IP extraction (hardcoded logic)
6. **Scheduler Thread Safety**: APScheduler uses thread pool; config reloaded on each run (no race conditions)
7. **Secrets**: `app.secret_key` regenerated on restart (safe for single container, unsafe for multi-replica)

## Domain Management per Forwarding Rule

Each forwarding rule can have multiple associated domains stored in `config.json["jobs"][job_id]["rule_domains"][rule_id]`:
```json
{
  "rule_domains": {
    "12345": ["server1.example.com", "app.example.com"],
    "12346": ["server2.example.com"]
  }
}
```

### Domain API Pattern
- `GET /api/domains/{job_id}/{rule_id}` - Retrieve all domains for a rule
- `POST /api/domains/{job_id}/{rule_id}` with `{"domains": [list]}` - Save domains list
- `DELETE /api/domains/{job_id}/{rule_id}` - Clear all domains for a rule

### UI Interaction
- Each rule row has a "🔗 域名" (Domain) button
- Modal shows current domains with delete buttons
- Users can add new domains via text input
- Changes saved to `config.json` on POST

## Extension Points

- **New job type**: Add conditional branches in `run_job` based on job field
- **New API endpoint**: Add `@app.route` with `@require_auth` decorator
- **UI enhancement**: Edit `src/static/index.html`, fetch/POST to existing `/api/config` endpoint
- **Notifications**: Extend beyond Telegram by adding similar pattern to `send_telegram_message`
- **Rule-level features**: Extend `rule_domains` pattern for metadata like notes, custom DNS settings, etc.
