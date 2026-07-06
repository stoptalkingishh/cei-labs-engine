"""docker/orchestrator/app/wsgi.py

Real production entry point (gunicorn app.wsgi:app). Kept separate from
main.py so importing main.py — e.g. in tests, which construct create_app()
themselves with an injected fake Docker client — never triggers a real
connection attempt to /var/run/docker.sock as a module-level side effect.
"""
from .main import create_app

app = create_app()
