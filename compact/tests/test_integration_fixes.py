"""Regressions for the integration review: auth checks, local-only guard, settings help, events index, trash lock."""
from __future__ import annotations

import ast
import time
from pathlib import Path

from emaraai_hub.api.access import LocalOnly
from emaraai_hub.drivers.extension import ExtensionBridge
from emaraai_hub.infra.db import Database


def test_extension_token_with_non_ascii_is_refused_not_crashing():
    bridge = ExtensionBridge()
    bridge.token = "abc"
    assert bridge.authorized("abc")
    assert not bridge.authorized("ébc")          # used to raise TypeError (an HTTP 500)
    assert not bridge.authorized("")


def test_extension_numeric_nonce_cannot_be_replayed():
    bridge = ExtensionBridge()
    now_ms = time.time() * 1000
    assert bridge.fresh(7, now_ms)
    assert not bridge.fresh(7, now_ms)           # the same number again is a replay
    assert not bridge.fresh("7", now_ms)


async def test_local_only_closes_a_remote_websocket():
    reached = []

    async def app(scope, receive, send):
        reached.append(scope["type"])

    sent = []

    async def send(msg):
        sent.append(msg)

    scope = {"type": "websocket", "client": ("203.0.113.5", 4000), "headers": [(b"host", b"example.com")]}
    await LocalOnly(app)(scope, None, send)
    assert reached == [] and sent == [{"type": "websocket.close", "code": 1008}]

    local = {"type": "websocket", "client": ("127.0.0.1", 4000), "headers": [(b"host", b"127.0.0.1:8797")]}
    await LocalOnly(app)(local, None, send)
    assert reached == ["websocket"]


def test_settings_help_has_no_duplicate_keys():
    src = Path(__file__).resolve().parents[1] / "src" / "emaraai_hub" / "infra" / "settings_store.py"
    for node in ast.walk(ast.parse(src.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Dict):
            keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            assert len(keys) == len(set(keys)), sorted({k for k in keys if keys.count(k) > 1})


def test_events_can_be_pruned_by_time_through_an_index(tmp_path):
    db = Database(tmp_path / "hub.sqlite3")
    db.migrate()
    plan = " ".join(r["detail"] for r in db.all("EXPLAIN QUERY PLAN DELETE FROM events WHERE ts < ?", (1.0,)))
    assert "ix_events_ts" in plan, plan


def test_foreign_keys_are_switched_only_while_holding_the_database_lock(tmp_path):
    from types import SimpleNamespace

    from emaraai_hub.services.vault import Vault

    db = Database(tmp_path / "hub.sqlite3")
    db.migrate()
    real, held = db._conn, []

    class Spy:                       # another thread must never see the foreign keys off: the switch happens under the lock
        def execute(self, sql, *a):
            if "foreign_keys" in sql:
                held.append(db._lock._is_owned())
            return real.execute(sql, *a)

        def __getattr__(self, name):
            return getattr(real, name)

    db._conn = Spy()
    try:
        Vault(SimpleNamespace(db=db), None, None, None, projects=None, sessions=None)._without_fk(lambda: None)
    finally:
        db._conn = real
    assert held == [True, True]
    assert db.one("PRAGMA foreign_keys")["foreign_keys"] == 1
