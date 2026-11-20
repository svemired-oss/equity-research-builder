import pytest


@pytest.fixture(autouse=True)
def isolated_provider_settings(monkeypatch):
    for key in ("SEC_USER_AGENT", "OPENAI_API_KEY", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "CODESPACES"):
        monkeypatch.delenv(key, raising=False)
