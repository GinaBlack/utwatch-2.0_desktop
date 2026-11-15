"""Reports and CSV export, all computed from the local database (no Qt, no cloud)."""
from __future__ import annotations
import csv
import html
import time
from collections import Counter
from pathlib import Path

from .models import Severity


def build_report(database, since: float, until: float, title: str = "Traffic report") -> dict:
    alerts = database.query_alerts(since=since, until=until, limit=100000)
    stats = [s for s in database.stats_since(since) if s["minute_ts"] <= until]
    cams = {c["id"]: c["name"] for c in database.cameras()}
    by_type = Counter(a["type"] for a in alerts if a["stage"] in ("info", "confirmed"))
    suspected = sum(1 for a in alerts if a["stage"] == "suspected")
    confirmed = sum(1 for a in alerts if a["stage"] == "confirmed")
    expired = sum(1 for a in alerts if a["stage"] == "expired")
    per_cam = {}
    for cid, name in cams.items():
        rows = [s for s in stats if s["camera_id"] == cid]
        per_cam[name] = {
            "avg_vehicles": round(sum(r["vehicles_avg"] for r in rows) / len(rows), 1) if rows else 0.0,
            "peak_vehicles": max((r["vehicles_max"] for r in rows), default=0),
            "high_congestion_minutes": sum(1 for r in rows if r["congestion"] == "high"),
            "incidents": sum(1 for a in alerts if a["camera_id"] == cid and a["stage"] == "confirmed"),
        }
    return {"title": title, "since": since, "until": until, "alerts_total": len(alerts), "suspected": suspected,
            "confirmed": confirmed, "expired": expired, "by_type": dict(by_type), "per_camera": per_cam,
            "generated": time.time()}


def _fmt(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def report_html(r: dict) -> str:
    rows = "".join(
        f"<tr><td>{html.escape(n)}</td><td>{v['avg_vehicles']}</td><td>{v['peak_vehicles']}</td>"
        f"<td>{v['high_congestion_minutes']}</td><td>{v['incidents']}</td></tr>" for n, v in r["per_camera"].items())
    types = "".join(f"<li>{html.escape(k.replace('_', ' ').capitalize())}: {v}</li>" for k, v in sorted(r["by_type"].items()))
    return (f"<h2>{html.escape(r['title'])}</h2><p>{_fmt(r['since'])} to {_fmt(r['until'])}</p>"
            f"<p>Confirmed accidents: <b>{r['confirmed']}</b>. Suspected: {r['suspected']}. "
            f"Suspected but not confirmed: {r['expired']}.</p>"
            f"<h3>Events by type</h3><ul>{types or '<li>None</li>'}</ul>"
            "<h3>Cameras</h3><table border='1' cellspacing='0' cellpadding='4'>"
            "<tr><th>Camera</th><th>Average vehicles</th><th>Peak vehicles</th><th>Minutes of heavy congestion</th>"
            f"<th>Confirmed accidents</th></tr>{rows}</table>")


def export_alerts_csv(alerts: list[dict], path: str | Path) -> int:
    cols = ["ts", "time", "camera_name", "type", "stage", "severity", "message", "incident_id", "ack_by"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for a in alerts:
            w.writerow([a["ts"], _fmt(a["ts"]), a["camera_name"], a["type"], a["stage"],
                        Severity(a["severity"]).label, a["message"], a["incident_id"], a["ack_by"]])
    return len(alerts)


def export_report_csv(r: dict, path: str | Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Report", r["title"], _fmt(r["since"]), _fmt(r["until"])])
        w.writerow(["Confirmed accidents", r["confirmed"]])
        w.writerow(["Suspected", r["suspected"]])
        w.writerow(["Not confirmed", r["expired"]])
        w.writerow([])
        w.writerow(["Camera", "Average vehicles", "Peak vehicles", "Minutes of heavy congestion", "Confirmed accidents"])
        for n, v in r["per_camera"].items():
            w.writerow([n, v["avg_vehicles"], v["peak_vehicles"], v["high_congestion_minutes"], v["incidents"]])
