"""
The hosted product's web app (`cps web`). Needs the `web` extra:

    pip install -e ".[web]"

See `app.py` for what it does and the security model it keeps.
"""

from .app import Config, create_app

__all__ = ["Config", "create_app"]
