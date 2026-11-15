"""SQLite storage: users, cameras, alerts, recordings, statistics, settings, audit.

One Database object per thread (SQLite connections are not shared between threads).
"""
from __future__ import annotations
import json
import sqlite3
import time
from pathlib import Path

from . import config, security
from .models import ROLES

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, salt BLOB NOT NULL, pw_hash TEXT NOT NULL,
  role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, approved INTEGER NOT NULL DEFAULT 1,
  failed INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS cameras(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, source TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
  lat REAL, lon REAL, map_x REAL, map_y REAL, speed_limit_kmh REAL, calibration TEXT, zones TEXT);
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, camera_id INTEGER, camera_name TEXT, type TEXT NOT NULL,
  severity INTEGER NOT NULL, stage TEXT NOT NULL, incident_id TEXT, message TEXT, details TEXT,
  ack_by TEXT, ack_ts REAL);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);
CREATE TABLE IF NOT EXISTS recordings(
  id INTEGER PRIMARY KEY, camera_id INTEGER, camera_name TEXT, path TEXT NOT NULL, thumb TEXT, ts REAL NOT NULL,
  duration REAL, size INTEGER, incident_id TEXT, kind TEXT, protected INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS stats(
  id INTEGER PRIMARY KEY, camera_id INTEGER, minute_ts INTEGER NOT NULL, vehicles_avg REAL, vehicles_max INTEGER,
  congestion TEXT);
CREATE INDEX IF NOT EXISTS idx_stats_ts ON stats(minute_ts);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts REAL NOT NULL, username TEXT, action TEXT NOT NULL, target TEXT);
"""


class AuthError(Exception):
    pass


class Database:
    def __init__(self, path: str | Path | None = None):
        self.path = str(path or (config.data_dir() / "utwatch.db"))
        self.conn = sqlite3.connect(self.path, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ---------- settings
    def get_settings(self) -> dict:
        out = config.defaults()
        for r in self.conn.execute("SELECT key, value FROM settings"):
            if r["key"] in out:
                try:
                    out[r["key"]] = config.validate_setting(r["key"], json.loads(r["value"]))
                except ValueError:
                    pass
        return out

    def set_setting(self, key: str, value):
        v = config.validate_setting(key, value)
        self.conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (key, json.dumps(v)))
        self.conn.commit()
        return v

    # ---------- users
    def user_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def create_user(self, username: str, password: str, role: str = "officer", approved: bool = True):
        username = username.strip()
        if not username:
            raise ValueError("Enter a username")
        if role not in ROLES:
            raise ValueError("Unknown role")
        security.check_password_rules(password)
        salt = security.new_salt()
        try:
            self.conn.execute(
                "INSERT INTO users(username,salt,pw_hash,role,approved,created) VALUES(?,?,?,?,?,?)",
                (username, salt, security.hash_password(password, salt), role, int(approved), time.time()))
        except sqlite3.IntegrityError:
            raise ValueError("That username is taken")
        self.conn.commit()

    def authenticate(self, username: str, password: str) -> dict:
        row = self.conn.execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
        if row is None:
            raise AuthError("Wrong username or password")
        now = time.time()
        if row["locked_until"] > now:
            mins = int((row["locked_until"] - now) // 60) + 1
            raise AuthError(f"Account locked. Try again in {mins} min or ask an administrator to reset it")
        if not row["active"]:
            raise AuthError("This account is disabled")
        if not row["approved"]:
            raise AuthError("This account is waiting for administrator approval")
        if not security.verify_password(password, row["salt"], row["pw_hash"]):
            failed = row["failed"] + 1
            locked = security.lock_until(now) if failed >= security.MAX_FAILED else 0
            self.conn.execute("UPDATE users SET failed=?, locked_until=? WHERE id=?",
                              (0 if locked else failed, locked, row["id"]))
            self.conn.commit()
            raise AuthError("Wrong username or password")
        self.conn.execute("UPDATE users SET failed=0, locked_until=0 WHERE id=?", (row["id"],))
        self.conn.commit()
        return {"id": row["id"], "username": row["username"], "role": row["role"]}

    def list_users(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT id,username,role,active,approved,failed,locked_until,created FROM users ORDER BY username")]

    def update_user(self, user_id: int, **fields):
        allowed = {"role", "active", "approved"}
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                raise ValueError("Cannot change " + k)
            if k == "role" and v not in ROLES:
                raise ValueError("Unknown role")
            sets.append(f"{k}=?")
            vals.append(v)
        if sets:
            self.conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=?", (*vals, user_id))
            self.conn.commit()

    def reset_password(self, user_id: int, new_password: str):
        security.check_password_rules(new_password)
        salt = security.new_salt()
        self.conn.execute("UPDATE users SET salt=?, pw_hash=?, failed=0, locked_until=0 WHERE id=?",
                          (salt, security.hash_password(new_password, salt), user_id))
        self.conn.commit()

    # ---------- cameras
    def add_camera(self, name: str, source: str, lat=None, lon=None, speed_limit=None) -> int:
        name, source = name.strip(), source.strip()
        if not name or not source:
            raise ValueError("Enter a camera name and a source")
        if lat not in (None, "") or lon not in (None, ""):
            lat, lon = config.validate_latlon(lat, lon)
        else:
            lat = lon = None
        try:
            cur = self.conn.execute("INSERT INTO cameras(name,source,lat,lon,speed_limit_kmh) VALUES(?,?,?,?,?)",
                                    (name, source, lat, lon, speed_limit))
        except sqlite3.IntegrityError:
            raise ValueError("A camera with that name already exists")
        self.conn.commit()
        return cur.lastrowid

    def update_camera(self, cam_id: int, **fields):
        allowed = {"name", "source", "enabled", "lat", "lon", "map_x", "map_y", "speed_limit_kmh", "calibration", "zones"}
        sets, vals = [], []
        if "lat" in fields or "lon" in fields:
            if fields.get("lat") not in (None, "") or fields.get("lon") not in (None, ""):
                fields["lat"], fields["lon"] = config.validate_latlon(fields.get("lat"), fields.get("lon"))
        for k, v in fields.items():
            if k not in allowed:
                raise ValueError("Cannot change " + k)
            sets.append(f"{k}=?")
            vals.append(json.dumps(v) if k in ("calibration", "zones") and v is not None and not isinstance(v, str) else v)
        if sets:
            self.conn.execute(f"UPDATE cameras SET {', '.join(sets)} WHERE id=?", (*vals, cam_id))
            self.conn.commit()

    def delete_camera(self, cam_id: int):
        self.conn.execute("DELETE FROM cameras WHERE id=?", (cam_id,))
        self.conn.commit()

    def cameras(self, only_enabled=False):
        q = "SELECT * FROM cameras" + (" WHERE enabled=1" if only_enabled else "") + " ORDER BY name"
        out = []
        for r in self.conn.execute(q):
            d = dict(r)
            for k in ("calibration", "zones"):
                d[k] = json.loads(d[k]) if d[k] else None
            out.append(d)
        return out

    # ---------- alerts
    def add_alert(self, a: dict) -> int:
        cur = self.conn.execute(
            "INSERT INTO alerts(ts,camera_id,camera_name,type,severity,stage,incident_id,message,details) VALUES(?,?,?,?,?,?,?,?,?)",
            (a["ts"], a.get("camera_id"), a.get("camera_name"), a["type"], int(a["severity"]), a["stage"],
             a.get("incident_id"), a.get("message", ""), config.dumps(a.get("details", {}))))
        self.conn.commit()
        return cur.lastrowid

    def acknowledge(self, alert_id: int, username: str):
        self.conn.execute("UPDATE alerts SET ack_by=?, ack_ts=? WHERE id=?", (username, time.time(), alert_id))
        self.conn.commit()

    def query_alerts(self, camera_id=None, type_=None, min_severity=None, since=None, until=None, limit=500, offset=0):
        q, p = "SELECT * FROM alerts WHERE 1=1", []
        for col, val, op in (("camera_id", camera_id, "="), ("type", type_, "="), ("severity", min_severity, ">="),
                             ("ts", since, ">="), ("ts", until, "<=")):
            if val not in (None, ""):
                q += f" AND {col} {op} ?"
                p.append(val)
        q += " ORDER BY ts DESC LIMIT ? OFFSET ?"
        p += [limit, offset]
        return [dict(r) for r in self.conn.execute(q, p)]

    # ---------- recordings
    def add_recording(self, r: dict) -> int:
        cur = self.conn.execute(
            "INSERT INTO recordings(camera_id,camera_name,path,thumb,ts,duration,size,incident_id,kind) VALUES(?,?,?,?,?,?,?,?,?)",
            (r.get("camera_id"), r.get("camera_name"), r["path"], r.get("thumb"), r["ts"], r.get("duration"),
             r.get("size"), r.get("incident_id"), r.get("kind", "event")))
        self.conn.commit()
        return cur.lastrowid

    def recordings(self, camera_id=None, limit=500):
        q, p = "SELECT * FROM recordings", []
        if camera_id:
            q += " WHERE camera_id=?"
            p.append(camera_id)
        q += " ORDER BY ts DESC LIMIT ?"
        p.append(limit)
        return [dict(r) for r in self.conn.execute(q, p)]

    def adopt_orphan_recordings(self, folder) -> int:
        """Add clips that are on disk but missing from the database (for example after a crash)."""
        known = {r["path"] for r in self.conn.execute("SELECT path FROM recordings")}
        added = 0
        for f in sorted(Path(folder).glob("*.mp4")):
            if str(f) in known:
                continue
            meta = {}
            side = Path(str(f) + ".json")
            if side.exists():
                try:
                    meta = json.loads(side.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    meta = {}
            thumb = f.with_suffix(".jpg")
            self.add_recording({"camera_name": meta.get("camera", f.stem.split("_")[0]), "path": str(f),
                                "thumb": str(thumb) if thumb.exists() else None,
                                "ts": meta.get("start", f.stat().st_mtime), "duration": meta.get("duration"),
                                "size": f.stat().st_size, "incident_id": meta.get("incident_id"),
                                "kind": "manual" if f.stem.endswith("_manual") else "event"})
            added += 1
        return added

    def set_protected(self, rec_id: int, value: bool):
        self.conn.execute("UPDATE recordings SET protected=? WHERE id=?", (int(value), rec_id))
        self.conn.commit()

    def delete_recording(self, rec_id: int):
        row = self.conn.execute("SELECT path,thumb FROM recordings WHERE id=?", (rec_id,)).fetchone()
        if row:
            for f in (row["path"], row["thumb"], (row["path"] or "") + ".json"):
                try:
                    if f:
                        Path(f).unlink(missing_ok=True)
                except OSError:
                    pass
            self.conn.execute("DELETE FROM recordings WHERE id=?", (rec_id,))
            self.conn.commit()

    def enforce_storage(self, quota_gb: float, retention_days: int) -> int:
        """Delete expired and over-quota unprotected recordings, oldest first. Returns how many were deleted."""
        deleted = 0
        cutoff = time.time() - retention_days * 86400
        for r in self.conn.execute("SELECT id FROM recordings WHERE protected=0 AND ts<?", (cutoff,)).fetchall():
            self.delete_recording(r["id"]); deleted += 1
        quota = int(quota_gb * 1e9)
        total = self.conn.execute("SELECT COALESCE(SUM(size),0) FROM recordings").fetchone()[0]
        if total > quota:
            for r in self.conn.execute("SELECT id,size FROM recordings WHERE protected=0 ORDER BY ts ASC").fetchall():
                if total <= quota:
                    break
                total -= r["size"] or 0
                self.delete_recording(r["id"]); deleted += 1
        return deleted

    # ---------- stats
    def add_stat(self, camera_id, minute_ts, avg, mx, congestion):
        self.conn.execute("INSERT INTO stats(camera_id,minute_ts,vehicles_avg,vehicles_max,congestion) VALUES(?,?,?,?,?)",
                          (camera_id, int(minute_ts), avg, mx, congestion))
        self.conn.commit()

    def stats_since(self, since: float, camera_id=None):
        q, p = "SELECT * FROM stats WHERE minute_ts>=?", [int(since)]
        if camera_id:
            q += " AND camera_id=?"; p.append(camera_id)
        return [dict(r) for r in self.conn.execute(q + " ORDER BY minute_ts", p)]

    # ---------- audit
    def audit(self, username: str | None, action: str, target: str = ""):
        self.conn.execute("INSERT INTO audit(ts,username,action,target) VALUES(?,?,?,?)",
                          (time.time(), username, action, target))
        self.conn.commit()

    def audit_log(self, limit=500):
        return [dict(r) for r in self.conn.execute("SELECT * FROM audit ORDER BY ts DESC LIMIT ?", (limit,))]
