from types import SimpleNamespace

import runtime.infrastructure.database as database_module


def test_synchronized_resolves_facade_time_binding_at_call_time(
    db,
    caplog,
    monkeypatch,
) -> None:
    db._lock_warn_threshold_seconds = 0.5
    caplog.set_level("WARNING", logger="happyranch.database.lock")

    with monkeypatch.context() as patched:
        ticks = iter(float(value) for value in range(8))
        patched.setattr(
            database_module,
            "_time",
            SimpleNamespace(monotonic=lambda: next(ticks)),
        )

        assert "get_org_setting" not in database_module.Database.__dict__
        assert db.get_org_setting("missing") is None
        assert "execute" in database_module.Database.__dict__
        list(db.execute("SELECT 1"))

    messages = [record.getMessage() for record in caplog.records]
    assert any("for Database.get_org_setting" in message for message in messages)
    assert any("for Database.execute" in message for message in messages)
