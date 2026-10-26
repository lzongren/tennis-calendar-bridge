"""Small resource-backed map page with base-path-safe local assets."""

import html
from hashlib import sha256
from importlib.resources import files

from .models import Config


ASSETS = {
    "walls.css": "text/css; charset=utf-8",
    "walls.js": "text/javascript; charset=utf-8",
    "vendor/lucide/maximize.svg": "image/svg+xml",
    "vendor/leaflet/leaflet.js": "text/javascript; charset=utf-8",
    "vendor/leaflet/leaflet.css": "text/css; charset=utf-8",
    "vendor/leaflet/images/layers.png": "image/png",
    "vendor/leaflet/images/layers-2x.png": "image/png",
}


def walls_html(config: Config) -> str:
    home = html.escape(config.app.base_path + "/", quote=True)
    static = files("tennis_overview").joinpath("static")
    discovery_link = (
        f'<a href="{home}discover">Find courses</a>' if config.app.discovery_enabled else ""
    )
    return (
        static.joinpath("walls.html")
        .read_text(encoding="utf-8")
        .replace("__HOME__", home)
        .replace("__DISCOVERY__", discovery_link)
        .replace(
            "__SCRIPT_VERSION__", sha256(static.joinpath("walls.js").read_bytes()).hexdigest()[:12]
        )
        .replace(
            "__STYLE_VERSION__", sha256(static.joinpath("walls.css").read_bytes()).hexdigest()[:12]
        )
    )


def wall_asset(path: str) -> tuple[bytes, str] | None:
    prefix = "/wall-assets/"
    if not path.startswith(prefix):
        return None
    name = path[len(prefix) :]
    if name not in ASSETS:
        return None
    return files("tennis_overview").joinpath("static", name).read_bytes(), ASSETS[name]
