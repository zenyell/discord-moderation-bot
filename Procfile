# Procfile is no longer used — Render uses render.yaml instead.
# Bot and dashboard are separate services. See render.yaml.
web: gunicorn dashboard:app --workers 1 --bind 0.0.0.0:$PORT --timeout 120
