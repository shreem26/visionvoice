"""VisionVoice dashboard: Flask JSON API + Server-Sent Events + static UI.

Every action goes through App.submit(), so the dashboard uses exactly the same
code path as voice commands, including the backend-only page check.
"""
from __future__ import annotations

import json
import queue
import threading
from pathlib import Path

from flask import Flask, Response, abort, jsonify, render_template, request

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
SAMPLES_DIR = Path("sample_pages")


def create_server(app) -> Flask:
    web = Flask(__name__, static_folder="static", template_folder="templates")
    web.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

    @web.get("/")
    def index():
        return render_template("index.html")

    @web.get("/api/state")
    def state():
        return jsonify(app.snapshot())

    @web.get("/api/history")
    def history():
        limit = min(int(request.args.get("limit", 50)), 500)
        return jsonify(app.history.recent(limit))

    @web.post("/api/command")
    def command():
        data = request.get_json(silent=True) or {}
        text = str(data.get("text", "")).strip()
        if not text:
            return jsonify(error={"code": "EMPTY_COMMAND", "message": "Type a command first."}), 400
        if len(text) > 300:
            return jsonify(error={"code": "COMMAND_TOO_LONG", "message": "That command is too long."}), 400
        app.submit("web", text)
        return jsonify(ok=True), 202

    @web.post("/api/capture")
    def capture():
        f = request.files.get("photo")
        if f:
            data = f.read()
            if f.mimetype not in ALLOWED_TYPES or not (data.startswith(b"\xff\xd8") or data.startswith(b"\x89PNG") or data[8:12] == b"WEBP"):
                return jsonify(error={"code": "UNSUPPORTED_TYPE", "message": "Upload a JPEG, PNG or WebP photo."}), 400
            app.submit("upload", (data, Path(f.filename or "photo").name))
            return jsonify(ok=True), 202
        data = request.get_json(silent=True) or {}
        sample = data.get("sample")
        if sample:
            path = (SAMPLES_DIR / Path(str(sample)).name)
            if not path.is_file():
                return jsonify(error={"code": "NOT_FOUND", "message": "Unknown sample page."}), 404
            app.submit("sample", str(path))
            return jsonify(ok=True), 202
        app.submit("web", "capture")
        return jsonify(ok=True), 202

    @web.post("/api/stop")
    def stop():
        app.speaker.stop()
        app.bus.publish("speech_stopped")
        return jsonify(ok=True)

    @web.get("/api/samples")
    def samples():
        if not SAMPLES_DIR.is_dir():
            return jsonify([])
        return jsonify(sorted(p.name for p in SAMPLES_DIR.glob("*.jpg")))

    @web.get("/captures/<cap_id>/<kind>.jpg")
    def capture_image(cap_id: str, kind: str):
        c = app.captures.get(cap_id)
        if not c or kind not in ("photo", "annotated") or not c.get(kind):
            abort(404)
        return Response(c[kind], mimetype="image/jpeg", headers={"Cache-Control": "max-age=3600"})

    @web.get("/api/events")
    def events():
        q = app.bus.subscribe()

        def stream():
            try:
                yield "retry: 2000\n\n"
                while True:
                    try:
                        ev = q.get(timeout=15)
                    except queue.Empty:
                        yield ": keep-alive\n\n"
                        continue
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
            finally:
                app.bus.unsubscribe(q)

        return Response(stream(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return web


def start_dashboard(app, host: str = "127.0.0.1", port: int = 8000) -> str:
    import logging

    from werkzeug.serving import make_server

    logging.getLogger("werkzeug").setLevel(logging.WARNING)  # keep the terminal readable

    web = create_server(app)
    srv = make_server(host, port, web, threaded=True)
    threading.Thread(target=srv.serve_forever, name="dashboard", daemon=True).start()
    shown = "127.0.0.1" if host in ("0.0.0.0", "") else host
    return f"http://{shown}:{srv.server_port}"
