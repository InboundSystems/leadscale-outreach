"""
Flask dashboard — stat API, contact list, tracking pixel endpoint,
unsubscribe handler, and session runner.
"""

import csv
import io
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request, Response

from ..config import DAILY_SEND_LIMIT, SECRET_KEY
from ..db import get_conn, get_daily_send_count
from ..tracker import record_open, record_unsubscribe
from ..warmup import warmup_status_report, is_warmup_complete

# 1×1 transparent PNG
_PIXEL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

PER_PAGE = 50


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.secret_key = SECRET_KEY

    # -----------------------------------------------------------------------
    # Tracking & unsubscribe
    # -----------------------------------------------------------------------

    @app.route("/track/open/<int:contact_id>/<int:step>")
    def track_open(contact_id: int, step: int):
        record_open(contact_id, step)
        return Response(_PIXEL_PNG, mimetype="image/png")

    @app.route("/unsubscribe/<int:contact_id>")
    def unsubscribe(contact_id: int):
        record_unsubscribe(contact_id)
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>Unsubscribed</title>"
            "<style>body{font-family:sans-serif;display:flex;align-items:center;"
            "justify-content:center;height:100vh;margin:0;background:#f8f9fa;}"
            ".box{text-align:center;padding:2rem 3rem;background:#fff;"
            "border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,.1);}"
            "h2{color:#198754;margin-bottom:.5rem;}"
            "p{color:#6c757d;margin:0;}</style></head>"
            "<body><div class='box'><h2>&#10003; Unsubscribed successfully</h2>"
            "<p>You won't receive any further emails from us.</p></div></body></html>"
        )

    # -----------------------------------------------------------------------
    # Dashboard UI
    # -----------------------------------------------------------------------

    @app.route("/")
    def index():
        return render_template("index.html")

    # -----------------------------------------------------------------------
    # API — stats
    # -----------------------------------------------------------------------

    @app.route("/api/stats")
    def api_stats():
        today = datetime.now().strftime("%Y-%m-%d")  # local date
        sent_today = get_daily_send_count(today)

        with get_conn() as conn:
            total_sent = conn.execute(
                "SELECT COUNT(*) FROM contacts WHERE status IN ('sent','replied')"
            ).fetchone()[0]
            queued = conn.execute(
                "SELECT COUNT(*) FROM contacts WHERE status = 'queued'"
            ).fetchone()[0]
            total_opened = conn.execute(
                "SELECT COUNT(*) FROM contacts WHERE opened = 1"
            ).fetchone()[0]
            total_replied = conn.execute(
                "SELECT COUNT(*) FROM contacts WHERE reply_received = 1"
            ).fetchone()[0]
            bounced = conn.execute(
                "SELECT COUNT(*) FROM contacts WHERE status = 'bounced'"
            ).fetchone()[0]

            # Follow-up pipeline counts
            pipeline = {}
            for step in range(0, 4):
                if step == 0:
                    count = conn.execute(
                        "SELECT COUNT(*) FROM contacts WHERE status='queued' AND sequence_step=0"
                    ).fetchone()[0]
                else:
                    count = conn.execute(
                        "SELECT COUNT(*) FROM contacts WHERE status='sent' AND sequence_step=? AND reply_received=0",
                        (step,),
                    ).fetchone()[0]
                pipeline[f"step_{step}"] = count

        warmup = warmup_status_report()
        open_rate = round(total_opened / total_sent * 100, 1) if total_sent else 0.0
        reply_rate = round(total_replied / total_sent * 100, 1) if total_sent else 0.0

        return jsonify({
            "total_sent": total_sent,
            "queued": queued,
            "open_rate_pct": open_rate,
            "reply_rate_pct": reply_rate,
            "bounced": bounced,
            "warmup_day": warmup["current_day"],
            "warmup_complete": warmup["is_complete"],
            "sent_today": sent_today,
            "daily_limit": DAILY_SEND_LIMIT,
            "pipeline": pipeline,
        })

    # -----------------------------------------------------------------------
    # API — contacts (paginated + filterable)
    # -----------------------------------------------------------------------

    @app.route("/api/contacts")
    def api_contacts():
        page = max(1, int(request.args.get("page", 1)))
        filter_by = request.args.get("filter", "all").lower()
        search = request.args.get("search", "").strip()

        offset = (page - 1) * PER_PAGE

        where_clauses = []
        params: list = []

        if filter_by != "all":
            where_clauses.append("status = ?")
            params.append(filter_by)

        if search:
            where_clauses.append("(business_name LIKE ? OR email LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        with get_conn() as conn:
            total_count = conn.execute(
                f"SELECT COUNT(*) FROM contacts {where_sql}", params
            ).fetchone()[0]

            rows = conn.execute(
                f"""
                SELECT id, business_name, email, status, sequence_step,
                       opened, reply_received, last_sent_at
                FROM contacts {where_sql}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                params + [PER_PAGE, offset],
            ).fetchall()

        contacts = [dict(r) for r in rows]
        return jsonify({
            "contacts": contacts,
            "total": total_count,
            "page": page,
            "per_page": PER_PAGE,
            "total_pages": max(1, -(-total_count // PER_PAGE)),  # ceiling div
        })

    # -----------------------------------------------------------------------
    # API — daily volume chart data
    # -----------------------------------------------------------------------

    @app.route("/api/daily-volume")
    def api_daily_volume():
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT date, emails_sent, opens, replies, bounces
                FROM daily_stats
                ORDER BY date DESC
                LIMIT 14
                """
            ).fetchall()
        # Return chronological order for the chart
        data = [dict(r) for r in reversed(rows)]
        return jsonify(data)

    # -----------------------------------------------------------------------
    # API — run session
    # -----------------------------------------------------------------------

    @app.route("/api/run-session", methods=["POST"])
    def api_run_session():
        if not is_warmup_complete():
            from ..warmup import run_warmup_session
            result = run_warmup_session()
            result["mode"] = "warmup"
        else:
            from ..campaign import run_campaign_session
            result = run_campaign_session()
            result["mode"] = "campaign"
        return jsonify(result)

    # -----------------------------------------------------------------------
    # API — CSV export
    # -----------------------------------------------------------------------

    @app.route("/api/export-csv")
    def api_export_csv():
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, business_name, email, status, sequence_step,
                       opened, reply_received, last_sent_at, created_at
                FROM contacts
                ORDER BY created_at DESC
                """
            ).fetchall()

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "id", "business_name", "email", "status", "sequence_step",
            "opened", "reply_received", "last_sent_at", "created_at",
        ])
        for row in rows:
            writer.writerow(list(row))

        output = buf.getvalue()
        return Response(
            output,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=contacts.csv"},
        )

    return app
