"""Smoke test: the real Streamlit app script runs without an exception (demo mode, temp data)."""
import socket

from conftest import FakeEngine
from streamlit.testing.v1 import AppTest

from config.defaults import PROJECT_ROOT
from config.settings import Settings


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_app_renders_without_exceptions(tmp_path, monkeypatch):
    import service as svc
    settings = Settings(mode="demo", data_dir=str(tmp_path), voice_enabled=False, overlay_port=free_port(),
                        udp_port=free_port())
    monkeypatch.setattr(svc, "load_settings", lambda: (settings, {}))
    monkeypatch.setattr(svc, "save_settings", lambda s: s.validate())
    monkeypatch.setattr(svc, "default_engine_factory", FakeEngine)
    monkeypatch.setattr(svc, "_service", None)
    try:
        at = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=30)
        at.run()
        assert not at.exception, [e.message for e in at.exception]
    finally:
        if svc._service is not None:
            svc._service.shutdown()
