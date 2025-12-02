# AlwaysOnTime

A Python-first web app for NYC transit updates using Flask, Jinja templates, HTMX, and Leaflet.
Minimal JavaScript; most logic in Python.

## Quick Start

```bash
# 1) Clone your empty GitHub repo locally (create it first on GitHub, name: AlwaysOnTime)
git clone AlwaysOnTime
cd AlwaysOnTime

# 2) Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3) Install dependencies
pip install -r requirements.txt

# 4) Configure env
cp .env.example .env
# Edit .env and add your MTA API key

# 5) Run
flask --app app.main run --port 5001 --debug
# open http://127.0.0.1:5001
```

## Tech
- Backend: Flask
- Templating: Jinja2
- Interactivity: HTMX (progressive enhancement, no front-end framework required)
- Map: Leaflet (tiny JS), tiles from OpenStreetMap
- Tests: pytest
- CI: GitHub Actions (Python)
- DB: SQLite (to be added later as needed)
- Auth: (Optional) Firebase or Flask-Login; stub left open

## Structure
```text
app/
  main.py           # Flask entry
  factory.py        # create_app()
  blueprints/
    status.py       # /api/status placeholder
  templates/
    base.html
    index.html
  static/
    css/main.css
    js/main.js
tests/
  test_health.py
docs/
  ROADMAP.md
  ARCHITECTURE.md
  API_CONTRACT.md
.github/workflows/ci.yml
requirements.txt
.env.example
.gitignore
```

## Collaborative Workflow (Simple)
1. Create issues for tasks. Assign owners and labels (backend/frontend/docs).
2. Branch from `main` using `feat/<topic>` or `fix/<topic>`.
3. Push branch and open a Pull Request.
4. CI must pass; get 1 review; then squash-merge.
5. Keep `.env` local; never commit secrets.

## Next Steps
- Fill `status.py` with real MTA calls (see docs at https://api.mta.info).
- Create endpoints for: service status, elevator/escalator availability, and alerts.
- Add simple SQLite models if you store favorites or alerts server-side.
