# NBA Contract Value Analyzer

Pull NBA player stats + salaries, compute a composite **value-per-dollar** score, and label players as **UNDERPAID / FAIR / OVERPAID** within height + salary-tier peer groups.

## What’s in here

- **`nba_analyzer.py`**: CLI entrypoint (fetch → analyze → write `nba_contracts_*.json`)
- **`fetcher.py`**: data pulls
  - player stats from `nba_api` (NBA.com stats endpoints)
  - salaries from HoopsHype (best-effort; may return a subset)
- **`analyzer.py`**: cleaning, normalization, scoring, peer ranking, verdict assignment, JSON output
- **`api.py`**: FastAPI server to serve `/api/*` and the static dashboard in `frontend/`
- **`frontend/`**: static HTML dashboard (served by `api.py`)

## Quickstart (CLI)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python nba_analyzer.py --season 2024
```

This writes a file like:

- `nba_contracts_YYYYMMDD_HHMMSS.json`

## Run the API + dashboard

```bash
source .venv/bin/activate
uvicorn api:app --reload --port 8000
```

- **API docs**: `http://localhost:8000/docs`
- **Players JSON**: `http://localhost:8000/api/players`
- **Dashboard**: `http://localhost:8000/`

To refresh data via the API:

```bash
curl -X POST "http://localhost:8000/api/refresh?season=2024"
```

## Notes / caveats

- **Network access**: `nba_api` calls NBA.com endpoints; you need outbound internet access.
- **Salaries**: HoopsHype is a Next.js app and may only embed a subset of salary records. If you want full coverage, replace the salary pull with a dedicated salary API.
- **Optional API key**: Copy `.env.example` → `.env` if you later wire a paid provider (not required for the current pipeline).

