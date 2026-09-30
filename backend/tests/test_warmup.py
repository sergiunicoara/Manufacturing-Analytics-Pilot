from app import warmup


def test_steps_run_in_order_and_are_reported(monkeypatch):
    order = []
    warmup.run({"a": lambda: order.append("a"), "b": lambda: order.append("b")})
    status = warmup.status()
    assert order == ["a", "b"] and status["steps"] == {"a": "ready", "b": "ready"} and status["error"] is None
    assert set(status["seconds"]) == {"a", "b"}


def test_a_failing_step_is_recorded_not_raised_and_stops_the_rest():
    order = []

    def boom():
        raise RuntimeError("Database schema is out of date")

    warmup.run({"a": boom, "b": lambda: order.append("b")})
    status = warmup.status()
    assert order == [] and status["steps"] == {"a": "failed", "b": "pending"}
    assert "out of date" in status["error"]


def test_warm_on_start_can_be_disabled(monkeypatch):
    monkeypatch.setenv("WARM_ON_START", "false")
    assert warmup.enabled() is False and warmup.start({"a": lambda: None}) is None
    monkeypatch.setenv("WARM_ON_START", "true")
    assert warmup.enabled() is True


def test_health_reports_warmup_without_needing_the_database():
    from app.main import health
    body = health()
    assert body["status"] == "ok" and "warmup" in body


def test_secured_health_does_not_leak_warmup_error_text(monkeypatch):
    import dataclasses
    from app import security
    from app.main import health
    warmup.run({"a": lambda: (_ for _ in ()).throw(RuntimeError("password=hunter2 host=internal-db"))})
    monkeypatch.setattr(security, "settings", dataclasses.replace(security.settings, app_profile="secured"))
    body = health()
    assert body["warmup"]["error"] == "see server log" and "hunter2" not in str(body)
