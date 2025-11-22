# Architecture (Python-first, minimal JS)

- Flask app renders pages via Jinja templates.
- HTMX enhances interactivity by swapping HTML fragments from Python endpoints.
- Leaflet is used for the map (small JS snippet to initialize the map div).
- Background polling (HTMX) or SSE endpoint for live updates.
- Secrets kept in `.env`; CI run without live keys.
