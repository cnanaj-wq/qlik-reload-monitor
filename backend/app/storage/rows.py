"""Conversion des lignes SQLite en objets du modèle."""

from __future__ import annotations

import json
import sqlite3

from ..models import ReloadEvent


def row_to_event(r: sqlite3.Row) -> ReloadEvent:
    return ReloadEvent(
        seq=r["seq"], reload_id=r["reload_id"], timestamp=r["timestamp"],
        source=r["source"], platform=r["platform"], app_id=r["app_id"],
        app_name=r["app_name"], event_type=r["event_type"], status=r["status"], section=r["section"],
        table=r["table_name"], rows=r["rows"], qvd=r["qvd_name"], qvd_path=r["qvd_path"],
        qvd_size_bytes=r["qvd_size_bytes"], message=r["message"],
        extra=json.loads(r["extra_json"] or "{}"),
    )
