"""Ingress web UI.

A single-page dashboard served through Home Assistant Ingress. All asset and
API references in the template are *relative* so they keep working behind the
dynamically-prefixed Ingress path.
"""

from __future__ import annotations

import logging

from flask import Flask, jsonify, render_template, request

from . import __version__
from .logsetup import recent_logs
from .manager import SyncManager

_LOGGER = logging.getLogger("gitsync.web")


def create_app(manager: SyncManager) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder=None)

    @app.get("/")
    def index():
        return render_template("index.html", version=__version__)

    @app.get("/api/status")
    def api_status():
        return jsonify(manager.status())

    @app.get("/api/logs")
    def api_logs():
        return jsonify({"lines": recent_logs(200)})

    @app.post("/api/sync")
    def api_sync():
        state = manager.sync(reason="ui")
        return jsonify(_action_response(state))

    @app.post("/api/restore")
    def api_restore():
        state = manager.restore(reason="ui")
        return jsonify(_action_response(state))

    @app.post("/api/switch")
    def api_switch():
        body = request.get_json(silent=True) or {}
        branch = str(body.get("branch", "")).strip()
        apply = _validate_apply(body.get("apply"))
        if not branch:
            return jsonify({"ok": False, "message": "Missing 'branch'"}), 400
        result = manager.switch_branch(branch, apply=apply, reason="ui")
        return jsonify(result)

    @app.post("/api/promote")
    def api_promote():
        body = request.get_json(silent=True) or {}
        apply = _validate_apply(body.get("apply"))
        result = manager.promote(apply=apply, reason="ui")
        return jsonify(result)

    @app.errorhandler(500)
    def _server_error(err):  # pragma: no cover - defensive
        _LOGGER.exception("Unhandled error in web request")
        return jsonify({"ok": False, "message": "Internal error"}), 500

    return app


_VALID_APPLY = ("none", "reload", "restart")


def _validate_apply(value) -> str:
    """Coerce a user-supplied apply action to a known value, defaulting to none."""
    value = str(value or "none").strip().lower()
    return value if value in _VALID_APPLY else "none"


def _action_response(state: dict) -> dict:
    result = state.get("last_result", "error")
    return {
        "ok": result in ("ok", "idle"),
        "result": result,
        "message": state.get("last_message", ""),
        "applied": state.get("last_applied", ""),
    }


def serve(app: Flask, host: str, port: int) -> None:
    """Run the app with the production-grade waitress WSGI server."""
    from waitress import serve as waitress_serve

    _LOGGER.info("Web UI listening on %s:%s", host, port)
    waitress_serve(app, host=host, port=port, threads=4)
