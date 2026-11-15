import pytest


@pytest.fixture(autouse=True)
def private_app_data(tmp_path, monkeypatch):
    # New projects read the user's saved default scope from here, and the workbench reports and switches the
    # dsh and Codex integrations; tests must not read or write the real ones.
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'private'))
    monkeypatch.setenv('DSH_HOME', str(tmp_path / 'dsh'))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
