"""Loopback-only, local chapter review server. All edits persist through pipeline API."""
from __future__ import annotations

import io
import math
import os
import json
import subprocess
import sys
from pathlib import Path
import threading
from collections import OrderedDict
from urllib.parse import urlparse

from flask import Flask, jsonify, request, send_file, g, has_request_context
from PIL import Image, ImageDraw



def seam_review_summary(join, qa_entry=None):
    """Keep physical seam evidence separate from the human inspection gate."""
    qa_entry = qa_entry or {}
    review = join.get("review") or {}
    checked = (qa_entry["human_checked"] is True if "human_checked" in qa_entry else
               review.get("reviewer") in {"user", "human"} and
               review.get("method") == "explicit_human_visual_review" and
               review.get("joins_checked", review.get("checked")) is True)
    kind = qa_entry.get("kind", "")
    risk = join.get("risk")
    flags = [str(f) for f in join.get("flags", []) if str(f) not in {"UNVERIFIED_JOIN", "JOINS_VISUALLY_CHECKED"}]
    if kind in {"risky_source_join", "preflight_source_adjacency_risk"} or risk == "suspicious" or join.get("blocked"):
        severity = "suspicious"
    elif kind == "uncertain_continuation_requires_inspection" or risk == "uncertain":
        severity = "uncertain"
    elif kind in {"ordinary_exact_row_append_unreviewed", "human_checked", "unused_source_adjacency_not_a_reconstruction_join"} or risk == "ordinary_continuation":
        severity = "ordinary"
    elif flags or join.get("assessment") in {"uncertain", "alignment_unknown", "missing_or_invalid_source_before_join"}:
        severity = "uncertain"
    else:
        severity = "ordinary"
    required = qa_entry.get("requires_human_inspection", not checked)
    return {"id": join.get("id"), "y": join.get("y", qa_entry.get("seam_y")),
            "assessment": join.get("assessment"), "severity": severity,
            "human_checked": checked, "requires_human_inspection": required,
            "human_review": "checked" if checked else "pending" if required else "not required for an unused adjacency",
            "priority": qa_entry.get("priority", "high" if severity == "suspicious" else "medium" if severity == "uncertain" else "low"),
            "evidence": qa_entry.get("evidence", flags),
            "source_ids": qa_entry.get("source_ids", [join.get("left_source"), join.get("right_source")])}


def panel_review_risk(panel, manifest, qa=None):
    """A review-queue weight, never a probability or artwork verification."""
    qa = qa or {}
    region = next((r for r in qa.get("per_region", []) if r.get("id") == panel.get("id")), {})
    flags = [str(f) for f in panel.get("flags", [])]
    warnings = flags + [str(w) for w in region.get("warnings", [])]
    qa_seams = {s.get("id"): s for s in qa.get("seam_risks", [])}
    joins = [seam_review_summary(j, qa_seams.get(j.get("id"))) for j in manifest.get("joins", [])
             if j.get("id") in panel.get("join_ids", [])]
    explicit_cut = any("CUTOFF" in w or "SEVERE_CUT" in w for w in warnings)
    internal_crossing = any(c.get("suspected_cut") and c.get("crossing_pixels", 0) >= 5
                            for c in region.get("linework_crossings", []) if isinstance(c, dict))
    cutoff = bool(explicit_cut or (region["cutoff_risk"] if "cutoff_risk" in region else internal_crossing))
    merge = panel.get("classification") == "compound_layout" or bool(panel.get("children")) or any(
        any(key in w for key in ("COMPOUND", "MULTIPLE_PANELS", "SEPARATED_LARGE_CONTENT")) for w in warnings)
    unclassified = panel.get("classification") in {None, "uncertain", "unclassified", "unclassified_content"} or "unclassified" in str(region.get("risk_type", ""))
    duplicate = any("DUPLICATE" in w for w in warnings) or any(panel.get("id") in d.get("ids", []) for d in qa.get("near_duplicates", []))
    source_lookup = {s["id"]:s for s in manifest.get("sources", [])}
    coordinates = not panel.get("source_regions")
    for piece in panel.get("source_regions", []):
        source = source_lookup.get(piece.get("source_id"))
        b = piece.get("source_bounds", [])
        if source is None or len(b) != 4 or any(isinstance(v,bool) or not isinstance(v,int) for v in b):
            coordinates = True
        elif not (0 <= b[0] < b[2] <= source["width"] and 0 <= b[1] < b[3] <= source["height"]):
            coordinates = True
    coordinates = coordinates or any(any(k in str(e) for k in ("COORDINATE", "SOURCE_BOUNDS", "OUTSIDE_SOURCE", "INVALID_REGION")) for e in region.get("errors", []))
    integrity = bool(region.get("errors")) or region.get("pixel_fidelity") == "FAIL"
    suspicious = any(j["severity"] == "suspicious" for j in joins)
    uncertain = any(j["severity"] == "uncertain" for j in joins)
    reasons = []
    score = 0
    for present, weight, code, text in ((coordinates, 100, "coordinates", "Source coordinates need checking"),
            (integrity, 100, "integrity", "Output integrity needs checking"),
            (cutoff, 90, "cutoff", "Possible cut across internal artwork"),
            (merge, 80, "merge", "Possible combined illustrations"),
            (suspicious, 75, "seam", "Suspicious source join"),
            (unclassified, 55, "unclassified", "Content needs classification"),
            (duplicate, 45, "duplicate", "Possible duplicate content"),
            (uncertain, 35, "uncertain_seam", "Uncertain source continuation"),
            ("USER_FLAGGED" in flags, 65, "flagged", "Flagged during review")):
        if present:
            score += weight
            reasons.append({"kind": code, "text": text})
    checked = (panel.get("status") == "VERIFIED" and
               panel.get("review", {}).get("reviewer") in {"user", "human"} and
               panel.get("review", {}).get("method") == "explicit_human_visual_review" and
               panel.get("review", {}).get("artwork_checked") is True)
    return {"id": panel.get("id"), "score": score, "level": "high" if score >= 75 else "medium" if score else "routine",
            "reasons": reasons, "categories": [r["kind"] for r in reasons], "seams": joins,
            "human_review": "checked" if checked else "rejected" if panel.get("status") == "REJECTED" else "pending",
            "ordinary_unreviewed_seams": sum(j["severity"] == "ordinary" and not j["human_checked"] and j["requires_human_inspection"] for j in joins),
            "suspicious_seams": sum(j["severity"] == "suspicious" for j in joins),
            "uncertain_seams": sum(j["severity"] == "uncertain" for j in joins),
            "source_coordinates": panel.get("source_regions", []), "qa_stale": bool(manifest.get("qa_stale")),
            "note": "Queue priority reflects review evidence; it does not establish illustration completeness."}

def create_app(output_dir=None):
    app = Flask(__name__, static_folder="static")
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
    state = {"output": Path(output_dir).expanduser().resolve() if output_dir else None,
             "job": None, "lock": threading.RLock(), "thread": None}

    previews = OrderedDict()
    cache_lock = threading.Lock()
    cache_bytes = 0

    def pipeline():
        from . import pipeline as p
        return p

    def output():
        root = getattr(g, "workspace", None) if has_request_context() else None
        root = root if root is not None else state["output"]
        if root is None:
            raise ValueError("Open an output folder or import a chapter first.")
        return root

    def manifest():
        try:
            return pipeline().load_manifest(output())
        except FileNotFoundError:
            job = persisted_job()
            sources = job.get("sources", [])
            if not sources:
                raise
            return {"sources": sources, "panels": [], "chapter": job.get("chapter", "Chapter"),
                    "chapter_width": max(s["width"] for s in sources), "total_height": max(s["y1"] for s in sources), "partial": True}

    def panel_by_id(m, panel_id):
        return next((p for p in m["panels"] if p["id"] == panel_id and p["status"] in {"NEEDS_REVIEW", "VERIFIED", "REJECTED", "ALTERNATIVE", "SUPERSEDED", "MERGED", "SPLIT", "IGNORED", "SOURCE_REVIEW"}), None)

    def require_panel(m, panel_id):
        p = panel_by_id(m, panel_id)
        if p is None:
            raise ValueError("Unknown panel ID.")
        return p

    @app.before_request
    def local_only():
        g.workspace = state["output"]
        host = urlparse("http://" + request.host).hostname
        if host not in {"127.0.0.1", "localhost", "::1"}:
            return jsonify(error="This app accepts localhost connections only."), 403
        origin = request.headers.get("Origin")
        if origin:
            parsed = urlparse(origin)
            if parsed.scheme != request.scheme or parsed.netloc != request.host:
                return jsonify(error="Cross-origin requests are disabled."), 403
        if request.method != "GET" and request.path != "/api/job/control" and merged_job().get("running"):
            return jsonify(error="Wait for the current chapter job to finish."), 409

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        return response

    @app.errorhandler(Exception)
    def handle_error(exc):
        from werkzeug.exceptions import HTTPException
        if isinstance(exc, HTTPException):
            return jsonify(error=exc.description), exc.code
        return jsonify(error=str(exc)), 400

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    def persisted_job():
        if state["output"] is None:
            return {}
        try:
            return pipeline().job_status(output()) or {}
        except (FileNotFoundError, AttributeError):
            return {}

    def merged_job():
        job = {**persisted_job(), **(state["job"] or {})}
        if not job.get("running") and job.get("status") in {"RUNNING", "RESUMING"}:
            pid = job.get("pid")
            if isinstance(pid, int) and pid != os.getpid():
                try:
                    os.kill(pid, 0)
                    job["running"] = True
                except OSError:
                    job["running"] = False
                    job["interrupted"] = True
        return job

    @app.get("/api/state")
    def get_state():
        return jsonify(output=str(state["output"] or ""), job=merged_job())

    @app.get("/api/job")
    def get_job():
        return jsonify(merged_job())

    @app.post("/api/job/control")
    def control_job():
        data = request.get_json(force=True)
        action = data.get("action")
        if action not in {"pause", "cancel", "resume", "retry"}:
            raise ValueError("Choose pause, cancel, resume, or retry.")
        job = pipeline().job_control(output(), "resume" if action == "retry" else action)
        if action in {"resume", "retry"} and not merged_job().get("running"):
            params = job.get("params", job.get("arguments", {}))
            src = params.get("input_path", job.get("input_path", job.get("input")))
            opts = dict(params.get("options", job.get("options", job.get("config", {}))))
            opts.pop("preflight", None)
            chapter = params.get("chapter", job.get("chapter", opts.pop("chapter", "CH01")))
            if not src:
                raise ValueError("Saved job has no input path; use Import with the same input/output to resume.")
            root = output()
            return launch_job(lambda: pipeline().process_chapter(src, root, chapter=chapter, **opts))
        return jsonify(job)

    @app.post("/api/open")
    def open_chapter():
        data = request.get_json(force=True)
        if not str(data.get("output", "")).strip():
            raise ValueError("Choose a saved output folder.")
        path = Path(data["output"]).expanduser().resolve()
        state["output"] = path
        g.workspace = path
        try:
            return jsonify(pipeline().load_manifest(path))
        except FileNotFoundError:
            job = persisted_job()
            if not job:
                raise ValueError("This folder has no saved chapter or resumable job.")
            return jsonify(job=job, sources=job.get("sources", []), panels=[], partial=True)

    @app.post("/api/choose")
    def choose_path():
        """Optional native chooser; pasted paths remain available on headless systems."""
        kind = request.get_json(force=True).get("kind", "input")
        if kind not in {"file", "folder", "output"}:
            raise ValueError("Choose file, folder, or output.")
        try:
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("picker.py")), kind],
                                    capture_output=True, text=True, timeout=300)
            data = json.loads(result.stdout)
            if result.returncode or "error" in data:
                raise ValueError(data.get("error", result.stderr.strip()))
            return jsonify(path=data.get("path", ""))
        except Exception as exc:
            return jsonify(error="Native chooser unavailable here. Paste the path instead. " + str(exc)), 400

    def launch_job(fn):
        state["job"] = {"running": True, "error": None}
        def run():
            try:
                with state["lock"]:
                    fn()
                state["job"] = {"running": False, "error": None}
            except Exception as exc:
                state["job"] = {"running": False, "error": None if exc.__class__.__name__ in {"JobPaused", "JobCancelled"} else str(exc)}
        state["thread"] = threading.Thread(target=run, daemon=True)
        state["thread"].start()
        return jsonify(started=True), 202

    @app.post("/api/import")
    def import_chapter():
        data = request.get_json(force=True)
        if not str(data.get("input", "")).strip() or not str(data.get("output", "")).strip():
            raise ValueError("Choose both an input and an output folder.")
        src = Path(data["input"]).expanduser().resolve()
        dest = Path(data["output"]).expanduser().resolve()
        if not src.exists():
            raise ValueError("Input path does not exist.")
        chapter = data.get("chapter", "CH01")
        state["output"] = dest  # Persisted progress is visible before final manifest exists.
        g.workspace = dest
        def work():
            pipeline().process_chapter(src, dest, chapter=chapter, include_all=bool(data.get("include_all", False)),
                                       min_gutter=int(data.get("min_gutter", 12)), padding=int(data.get("padding", 4)),
                                       vertical=bool(data.get("vertical", True)), reading_direction=data.get("reading_direction", "ltr"),
                                       preflight=bool(data.get("preflight", False)))
            state["output"] = dest
        return launch_job(work)

    @app.get("/api/manifest")
    def get_manifest():
        return jsonify(manifest())

    def context_bounds(m, p, extra=0):
        w = int(m.get("chapter_width", max(s["width"] for s in m["sources"])))
        h = int(m.get("total_height", max(s["y1"] for s in m["sources"])))
        x0, y0, x1, y1 = p["bounds"]
        padding = min(900, max(140, int((y1 - y0) * .15))) + extra
        return [0, max(0, y0 - padding), w, min(h, y1 + padding)]

    @app.get("/api/panels/<panel_id>")
    def get_panel(panel_id):
        m = manifest()
        p = require_panel(m, panel_id)
        extra = min(5000, max(0, request.args.get("extra", 0, type=int)))
        bounds = context_bounds(m, p, extra)
        sources = [{k: s[k] for k in ("id", "original_name", "width", "height", "y0", "y1") if k in s}
                   for s in m["sources"] if s["y0"] < bounds[3] and s["y1"] > bounds[1]]
        indexes = [i for i,s in enumerate(m["sources"]) if s["y0"] < p["bounds"][3] and s["y1"] > p["bounds"][1]]
        neighbors = m["sources"][max(0,min(indexes)-1):min(len(m["sources"]),max(indexes)+2)] if indexes else []
        neighbor_meta = [{k: source[k] for k in ("id", "order", "original_name", "width", "height", "y0", "y1") if k in source}
                         for source in neighbors]
        qa = m.get("qa", {})
        neighbor_ids = {s["id"] for s in neighbors}
        qa_seams = {j.get("id"):j for j in qa.get("seam_risks", [])}
        neighbor_joins = [seam_review_summary(j,qa_seams.get(j.get("id"))) for j in m.get("joins", [])
                          if j.get("right_source") in neighbor_ids]
        return jsonify(panel=p, context_bounds=bounds, sources=sources, neighboring_sources=neighbor_meta,
                       neighbor_joins=neighbor_joins, risk=panel_review_risk(p,m,qa))

    @app.get("/api/risk")
    def risk_queue():
        m = manifest()
        qa = m.get("qa", {})
        regions = [panel_review_risk(p,m,qa) for p in m.get("panels", []) if p.get("status") in {"NEEDS_REVIEW", "VERIFIED", "REJECTED", "ALTERNATIVE"}]
        regions.sort(key=lambda r: (-r["score"], r["id"]))
        return jsonify(regions=regions, note="Review priority and human inspection state are separate.")

    def preview_image(m, bounds, max_size=1600):
        """Resize separate intersecting strips into a bounded canvas, preserving originals."""
        x0, y0, x1, y1 = [int(v) for v in bounds]
        w, h = x1-x0, y1-y0
        if w <= 0 or h <= 0:
            raise ValueError("Invalid image bounds.")
        scale = min(1.0, max_size / max(w, h), math.sqrt(4_000_000 / (w*h)))
        out_w, out_h = max(1, round(w*scale)), max(1, round(h*scale))
        canvas = Image.new("RGB", (out_w, out_h), "white")
        for src in m["sources"]:
            sy0, sy1 = max(y0, src["y0"]), min(y1, src["y1"])
            sx0, sx1 = max(x0, 0), min(x1, src["width"])
            if sy1 <= sy0 or sx1 <= sx0:
                continue
            path = (output() / src["path"]).resolve()
            if not path.is_relative_to(output()):
                raise ValueError("Unsafe source path in manifest.")
            with Image.open(path) as image:
                if image.width * image.height > 80_000_000:
                    raise ValueError("Source exceeds the bounded 80 MP decode limit.")
                # Coordinates intentionally remain in stored raster space (no EXIF transform).
                section = image.crop((sx0, sy0-src["y0"], sx1, sy1-src["y0"])).convert("RGBA")
                dx0, dx1 = round((sx0-x0)*scale), round((sx1-x0)*scale)
                dy0, dy1 = round((sy0-y0)*scale), round((sy1-y0)*scale)
                if dx1 > dx0 and dy1 > dy0:
                    section = section.resize((dx1-dx0, dy1-dy0), Image.Resampling.LANCZOS)
                    canvas.paste(section, (dx0, dy0), section.getchannel("A"))
                section.close()
        return canvas

    def preview_response(m, bounds, max_size=1600, polygon=None):
        nonlocal cache_bytes
        fingerprints = []
        for source in m["sources"]:
            if source["y0"] < bounds[3] and source["y1"] > bounds[1]:
                source_path = (output()/source["path"]).resolve()
                if not source_path.is_relative_to(output()):
                    raise ValueError("Unsafe source path in manifest.")
                stat = source_path.stat()
                fingerprints.append((source["id"], source.get("sha256"), source["y0"], source["y1"], stat.st_mtime_ns, stat.st_size))
        key = (str(output()), tuple(bounds), max_size, json.dumps(polygon), tuple(fingerprints))
        with cache_lock:
            data = previews.pop(key, None)
            if data is not None:
                previews[key] = data
        if data is None:
            im = preview_image(m, bounds, max_size=max_size)
            if polygon:
                mask = Image.new("L", im.size, 0)
                points = [((x-bounds[0])*im.width/(bounds[2]-bounds[0]), (y-bounds[1])*im.height/(bounds[3]-bounds[1])) for x,y in polygon]
                ImageDraw.Draw(mask).polygon(points, fill=255)
                rgba = im.convert("RGBA")
                rgba.putalpha(mask)
                im.close()
                mask.close()
                im = rgba
            buf = io.BytesIO()
            im.save(buf, "PNG")
            im.close()
            data = buf.getvalue()
            if len(data) <= 32 * 1024 * 1024:
                with cache_lock:
                    if key not in previews:
                        previews[key] = data
                        cache_bytes += len(data)
                    while previews and cache_bytes > 32 * 1024 * 1024:
                        _, old = previews.popitem(last=False)
                        cache_bytes -= len(old)
        return send_file(io.BytesIO(data), mimetype="image/png")

    @app.get("/api/preview/<panel_id>/<kind>")
    def preview(panel_id, kind):
        m = manifest()
        p = require_panel(m, panel_id)
        if kind not in {"context", "crop", "seam"}:
            raise ValueError("Invalid preview type.")
        extra = min(5000, max(0, request.args.get("extra", 0, type=int)))
        bounds = context_bounds(m, p, extra) if kind == "context" else p["bounds"]
        if kind == "seam":
            y = request.args.get("y", -1, type=int)
            if y not in {s["y0"] for s in m["sources"][1:]}:
                raise ValueError("Choose an existing source join.")
            bounds = [0, max(0, y-160), m["chapter_width"], min(m["total_height"], y+160)]
        return preview_response(m, bounds, max_size=min(1600, max(64, request.args.get("size", 1600, type=int))),
                                polygon=p.get("polygon") if kind == "crop" else None)

    @app.post("/api/panels/<panel_id>")
    def edit_panel(panel_id):
        data = request.get_json(force=True)
        with state["lock"]:
            pipeline().update_panel(output(), panel_id, bounds=data.get("bounds"), status=data.get("status"),
                                    checked=bool(data.get("checked", False)), joins_checked=bool(data.get("joins_checked", False)),
                                    reviewer="user", note=str(data.get("note", "")), polygon=data.get("polygon"))
        return jsonify(manifest())

    @app.post("/api/classify")
    def classify():
        data = request.get_json(force=True)
        with state["lock"]:
            pipeline().classify_panel(output(), data["id"], data["classification"], note=str(data.get("note", "")))
        return jsonify(manifest())

    @app.post("/api/merge")
    def merge():
        data = request.get_json(force=True)
        with state["lock"]:
            pipeline().merge_panels(output(), data["ids"])
        return jsonify(manifest())

    @app.post("/api/split")
    def split():
        data = request.get_json(force=True)
        with state["lock"]:
            pipeline().split_panel(output(), data["id"], data.get("coordinate", data.get("y")), axis=data.get("axis", "y"))
        return jsonify(manifest())

    @app.post("/api/reprocess")
    def reprocess():
        data = request.get_json(force=True)
        ids = data.get("ids", [])
        if not ids:
            raise ValueError("Select the failed panels to reprocess.")
        return launch_job(lambda: pipeline().reprocess_panels(output(), ids))

    @app.post("/api/sheets")
    def sheets():
        with state["lock"]:
            dest = output()
            with pipeline().writing(dest):
                m = pipeline().load_manifest(dest)
                pipeline().generate_sheets(dest, m)
                m["contact_sheets_stale"] = False
                pipeline().save_manifest(dest, m)
        return jsonify(m)

    @app.get("/api/source/<source_id>")
    def source_thumbnail(source_id):
        m = manifest()
        source = next((s for s in m["sources"] if s["id"] == source_id), None)
        if source is None:
            raise ValueError("Unknown source strip.")
        native = request.args.get("native") == "1"
        if native and source["width"] * source["height"] > 4_000_000:
            raise ValueError("This source is larger than the native preview limit; use the pixel viewport or open the original.")
        size = 4_000_000 if native else min(1600, max(32, request.args.get("size", 240, type=int)))
        return preview_response(m, [0, source["y0"], source["width"], source["y1"]], max_size=size)

    @app.get("/api/original/<source_id>")
    def original_source(source_id):
        m = manifest()
        source = next((s for s in m["sources"] if s["id"] == source_id), None)
        if source is None:
            raise ValueError("Unknown source strip.")
        path = (output()/source["path"]).resolve()
        if not path.is_relative_to(output()/"originals"):
            raise ValueError("Unsafe original source path.")
        return send_file(path, download_name=source.get("original_name", path.name))

    @app.get("/api/viewport")
    def native_viewport():
        m = manifest()
        bounds = [request.args.get(k, type=int) for k in ("x0", "y0", "x1", "y1")]
        from .chapter import Chapter
        bounds = Chapter(output(), m["sources"]).validate(bounds)
        thumbnail_size = request.args.get("size", type=int)
        if (bounds[2]-bounds[0])*(bounds[3]-bounds[1]) > 4_000_000 and not (thumbnail_size and 32 <= thumbnail_size <= 512):
            raise ValueError("Native viewport is limited to four million pixels; choose a smaller window.")
        return preview_response(m, bounds, max_size=min(4_000_000, max(32, request.args.get("size", 4_000_000, type=int))))

    @app.post("/api/undo")
    def undo():
        with state["lock"]:
            pipeline().undo(output())
        return jsonify(manifest())

    @app.post("/api/redo")
    def redo():
        with state["lock"]:
            pipeline().redo(output())
        return jsonify(manifest())

    @app.post("/api/reorder/<kind>")
    def reorder(kind):
        data = request.get_json(force=True)
        with state["lock"]:
            if kind == "panels":
                pipeline().reorder_panels(output(), data["ids"])
            elif kind == "sources":
                pipeline().reorder_sources(output(), data["ids"])
            else:
                raise ValueError("Choose panels or sources.")
        return jsonify(manifest())

    @app.post("/api/alternatives")
    def alternatives():
        data = request.get_json(force=True)
        with state["lock"]:
            pipeline().choose_alternative(output(), data["parent_id"], data["child_ids"])
        return jsonify(manifest())

    @app.post("/api/redetect")
    def redetect():
        ids = request.get_json(force=True).get("ids", [])
        if not ids:
            raise ValueError("Select regions for difference proposals.")
        return launch_job(lambda: pipeline().redetect_panels(output(), ids))

    @app.post("/api/flag")
    def flag():
        data = request.get_json(force=True)
        with state["lock"]:
            m = manifest()
            p = require_panel(m, data["id"])
            if "USER_FLAGGED" not in p.setdefault("flags", []):
                p["flags"].append("USER_FLAGGED")
            p["user_note"] = str(data.get("note", ""))
            with pipeline().writing(output()):
                pipeline().save_manifest(output(), m)
        return jsonify(m)

    def qa_report():
        for name in ("qa.json", "qa_report.json"):
            path = output()/"reports"/name
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        return manifest().get("qa", {"status": "QA_NOT_RUN", "warnings": ["Run chapter completeness QA before relying on these results."]})

    @app.get("/api/qa")
    def get_qa():
        return jsonify(qa_report())

    @app.post("/api/qa")
    def refresh_qa():
        with state["lock"]:
            pipeline().refresh_qa(output())
        return jsonify(qa_report())

    @app.get("/api/download/<panel_id>")
    def download(panel_id):
        p = require_panel(manifest(), panel_id)
        rel = p.get("file") or p.get("output_file")
        if not rel:
            raise ValueError("This panel has no exported file.")
        file_path = (output() / rel).resolve()
        if not any(file_path.is_relative_to(output() / folder) for folder in ("proposals", "needs_review", "approved_artwork", "cropped_panels", "review")):
            raise ValueError("Unsafe panel path in manifest.")
        return send_file(file_path, as_attachment=True, download_name=panel_id+".png")

    return app
