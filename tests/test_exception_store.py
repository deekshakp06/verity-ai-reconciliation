import os, sys
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from exception_store import ExceptionStore, dataset_key


@pytest.fixture
def store(tmp_path):
    return ExceptionStore(f"sqlite:///{tmp_path / 'test.db'}")


def test_set_and_get(store):
    store.set_review("d1", "TXN1", "resolved", "confirmed with bank", "Deeksha")
    r = store.get_reviews("d1")["TXN1"]
    assert (r["status"], r["note"], r["reviewer"]) == ("resolved", "confirmed with bank", "Deeksha")


def test_update_keeps_history(store):
    store.set_review("d1", "TXN1", "escalated", "needs finance lead")
    store.set_review("d1", "TXN1", "resolved", "lead approved")
    assert store.get_reviews("d1")["TXN1"]["status"] == "resolved"
    h = store.history("d1", "TXN1")
    assert [(e["from_status"], e["to_status"]) for e in h] == [("open", "escalated"), ("escalated", "resolved")]


def test_datasets_are_isolated(store):
    store.set_review("d1", "TXN1", "resolved")
    assert store.get_reviews("d2") == {}


def test_state_survives_new_store_instance(tmp_path):
    url = f"sqlite:///{tmp_path / 'x.db'}"
    ExceptionStore(url).set_review("d1", "TXN1", "escalated", "later")
    assert ExceptionStore(url).get_reviews("d1")["TXN1"]["status"] == "escalated"


def test_validation(store):
    with pytest.raises(ValueError):
        store.set_review("d1", "TXN1", "approved")
    with pytest.raises(ValueError):
        store.set_review("", "TXN1", "open")


def test_dataset_key_stable_and_sensitive():
    a = pd.DataFrame({"x": [1, 2], "y": ["a", "b"]})
    assert dataset_key(a) == dataset_key(a.copy())
    assert dataset_key(a) != dataset_key(pd.DataFrame({"x": [1, 3], "y": ["a", "b"]}))


def test_api_roundtrip(tmp_path, monkeypatch):
    import server
    monkeypatch.setattr(server, "_store", ExceptionStore(f"sqlite:///{tmp_path / 'api.db'}"))
    c = server.app.test_client()
    r = c.post("/api/review", json={"dataset_key": "d1", "payment_id": "TXN9", "status": "resolved", "note": "ok"})
    assert r.status_code == 200 and r.get_json()["status"] == "resolved"
    assert c.post("/api/review", json={"dataset_key": "d1", "payment_id": "TXN9", "status": "bogus"}).status_code == 400
    h = c.get("/api/review/d1/TXN9/history").get_json()
    assert h[0]["to_status"] == "resolved"


@pytest.mark.skipif(not os.environ.get("VERITY_TEST_PG_URL"), reason="set VERITY_TEST_PG_URL to run against PostgreSQL")
def test_works_on_postgresql():
    s = ExceptionStore(os.environ["VERITY_TEST_PG_URL"])
    s.set_review("pgtest", "TXN1", "escalated", "x")
    s.set_review("pgtest", "TXN1", "resolved", "y")
    assert s.get_reviews("pgtest")["TXN1"]["status"] == "resolved"
    assert len(s.history("pgtest", "TXN1")) >= 2


def test_server_falls_back_to_sqlite_when_database_unreachable(tmp_path, monkeypatch):
    import server
    monkeypatch.setattr(server, "_store", None)
    monkeypatch.setattr(server, "_db_error", None)
    monkeypatch.setenv("VERITY_DB_URL", "postgresql://u:p@nonexistent.invalid:5432/x")
    def fake(url=None):
        if os.environ.get("VERITY_DB_URL"):
            raise RuntimeError("database unreachable")
        return ExceptionStore(f"sqlite:///{tmp_path / 'fb.db'}")
    monkeypatch.setattr(server, "ExceptionStore", fake)
    store = server.get_store()
    assert store.engine.dialect.name == "sqlite" and server._db_error
    assert server.app.test_client().get("/api/nope").get_json()["error"]   # JSON, not an HTML page
