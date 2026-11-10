import pytest


@pytest.fixture(autouse=True)
def private_app_data(tmp_path, monkeypatch):
    # New projects read the user's saved default scope from here; tests must not read or write the real one.
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'private'))
