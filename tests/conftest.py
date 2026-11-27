import pytest


@pytest.fixture(autouse=True)
def _german_by_default(monkeypatch):
    """The older tests assert German texts; a test client without Accept-Language would get English."""
    from flask.testing import FlaskClient
    orig = FlaskClient.__init__

    def init(self, *a, **k):
        orig(self, *a, **k)
        self.environ_base["HTTP_ACCEPT_LANGUAGE"] = "de"
    monkeypatch.setattr(FlaskClient, "__init__", init)
