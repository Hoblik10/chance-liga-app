# AGENTS.md

## Cursor Cloud specific instructions

This is a single Python 3.12 **Streamlit** app (Czech-language football prediction portal for the Chance Liga). There is no database, container runtime, or background daemon — persistence is flat files (`predikce_log.csv`, `kurzy.csv`, `sezony/*.json`).

### Environment
- Dependencies are installed into a project virtualenv at `.venv` (git-ignored). The startup update script creates/refreshes it via `pip install -r requirements.txt`.
- Activate it before running anything: `source .venv/bin/activate` (or call binaries directly, e.g. `.venv/bin/streamlit`).
- Base image needs the `python3.12-venv` apt package for `python3 -m venv` to work; it is part of the saved environment, so the update script does not reinstall it.

### Run / test / build
- Run the web app: `streamlit run app.py` → serves on port **8501**. It fetches live data from TheSportsDB (public test key `"123"` by default) and falls back to scraping / computed / static data, so it works with no secrets.
- Tests: `python test_modely.py` (109 `unittest` cases, runs in <1s). This is the only automated test entry point.
- There is no configured linter/formatter; use `python -m py_compile <files>` for a quick syntax check if needed.
- Telegram tips CLI (optional batch job, no server): `python posli_hlaseni.py --suchy` for a dry run that prints tips without sending. Sending requires `TELEGRAM_TOKEN` + `TELEGRAM_CHAT_ID`.

### Configuration (all optional for the core web app)
- Config is read in `nastaveni.py` from Streamlit secrets (`.streamlit/secrets.toml`, git-ignored; template at `.streamlit/secrets.toml.example`) or from env vars when run as the scheduled job.
- Relevant keys: `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `SPORTSDB_KEY` (defaults to public test key `"123"`), `SEZONA`.
- Set `PYTHONUTF8=1` and `TZ=Europe/Prague` to match the GitHub Actions job when running the tips CLI.
