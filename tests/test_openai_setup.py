"""Offline credential setup: persistence, precedence, cancellation and disclosure boundaries."""

import importlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from synthshop.core.config import Settings
from synthshop.core.openai_setup import save_openai_key
from synthshop.web.app import create_app

cli = importlib.import_module("synthshop.cli.main")
INERT = "inert-not-a-real-key"


@pytest.fixture
def terminal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "sys", SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True)))
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: INERT)
    return tmp_path / ".env"


def invoke(answers="y\n"):
    return CliRunner().invoke(cli.app, ["setup-openai"], input=answers)


@pytest.mark.parametrize("typed", [INERT, f" {INERT}\t"])
def test_setup_persists_private_key_without_disclosure(terminal, monkeypatch, typed):
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: typed)
    before = Settings()
    result = invoke()
    assert result.exit_code == 0, result.output
    assert INERT not in result.output
    assert terminal.stat().st_mode & 0o777 == 0o600
    assert not before.readiness()["vision"]
    restarted = Settings()
    assert restarted.require_openai() == INERT
    assert restarted.readiness()["vision"]
    assert INERT not in restarted.model_dump_json()


@pytest.mark.parametrize(
    "placeholder", ["OPENAI_API_KEY=\n", "OPENAI_API_KEY\n", "OPENAI_API_KEY=''\n"]
)
def test_blank_placeholder_is_filled_without_replacement_prompt(terminal, placeholder):
    terminal.write_text(placeholder + "REVERB_API_TOKEN=inert-reverb\n")
    result = invoke("y\n")
    assert result.exit_code == 0, result.output
    assert terminal.read_text() == f"REVERB_API_TOKEN=inert-reverb\nOPENAI_API_KEY='{INERT}'\n"
    assert Settings().require_openai() == INERT


def test_setup_failures_report_distinct_reasons_without_disclosure(terminal, monkeypatch):
    secret = "inert-secret-marker"
    target = terminal.parent / "linked"
    target.write_text(f"REVERB_API_TOKEN={secret}\n")
    arrangements = [
        lambda: terminal.symlink_to(target),
        lambda: terminal.write_bytes(f"REVERB_API_TOKEN={secret}\xff\n".encode("latin-1")),
        lambda: terminal.write_text(f'REVERB_API_TOKEN="{secret}'),
        lambda: monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: f"{secret} !"),
    ]
    reasons = set()
    for arrange in arrangements:
        terminal.unlink(missing_ok=True)
        arrange()
        result = invoke()
        assert result.exit_code == 1
        assert secret not in result.output
        assert "xff" not in result.output
        reasons.add(result.output.strip().splitlines()[-1])
    assert len(reasons) == len(arrangements)
    assert not terminal.exists()
    assert not list(terminal.parent.glob(".env-*"))


def test_replacement_preserves_other_entries_and_handles_duplicate_case(terminal):
    preserved = '# keep comment\nREVERB_API_TOKEN="inert-reverb"\nCUSTOM="line one\nline two"\n'
    terminal.write_text(preserved + "openai_api_key=old\nexport OPENAI_API_KEY=older\n")
    result = invoke("y\ny\n")
    assert result.exit_code == 0
    assert terminal.read_text() == preserved + f"OPENAI_API_KEY='{INERT}'\n"
    settings = Settings()
    assert settings.require_openai() == INERT
    assert settings.require_reverb() == "inert-reverb"


@pytest.mark.parametrize("answers", ["n\n", "y\nn\n", "y\n"])
def test_cancel_keeps_existing_bytes_and_permissions(terminal, answers):
    original = b"OPENAI_API_KEY=old\nREVERB_API_TOKEN=untouched\n"
    terminal.write_bytes(original)
    terminal.chmod(0o640)
    result = invoke(answers)
    assert result.exit_code in (0, 1)
    assert terminal.read_bytes() == original
    assert terminal.stat().st_mode & 0o777 == 0o640
    assert not list(terminal.parent.glob(".env-*"))


@pytest.mark.parametrize("cancel", ["", EOFError(), KeyboardInterrupt()])
def test_hidden_prompt_cancel_creates_no_file(terminal, monkeypatch, cancel):
    def prompt(_prompt):
        if isinstance(cancel, BaseException):
            raise cancel
        return cancel
    monkeypatch.setattr(cli.getpass, "getpass", prompt)
    invoke()
    assert not terminal.exists()
    assert not list(terminal.parent.glob(".env-*"))


@pytest.mark.parametrize("value", ["inert-env-key", ""])
def test_environment_override_blocks_save_and_has_precedence(terminal, monkeypatch, value):
    terminal.write_text("OPENAI_API_KEY=file-key\n")
    original = terminal.read_bytes()
    monkeypatch.setenv("OPENAI_API_KEY", value)
    result = invoke()
    assert result.exit_code == 1
    assert terminal.read_bytes() == original
    assert "overrides .env" in result.output
    assert Settings().openai_api_key.get_secret_value() == value
    assert Settings(_env_file=None).openai_api_key.get_secret_value() == value
    monkeypatch.delenv("OPENAI_API_KEY")
    assert Settings().require_openai() == "file-key"
    assert not Settings(_env_file=None).readiness()["vision"]


@pytest.mark.parametrize("original", [b'BROKEN="unterminated', b"\xff", b"REVERB_API_TOKEN=kept\n"])
def test_failure_does_not_damage_existing_configuration(terminal, monkeypatch, original):
    terminal.write_bytes(original)
    def fail_replace(*_args):
        raise OSError(INERT)
    monkeypatch.setattr("synthshop.core.openai_setup.os.replace", fail_replace)
    result = invoke()
    assert result.exit_code == 1
    assert INERT not in result.output
    assert terminal.read_bytes() == original
    assert not list(terminal.parent.glob(".env-*"))


def test_symlink_is_refused_without_changing_target(terminal):
    target = terminal.parent / "protected"
    target.write_text("REVERB_API_TOKEN=kept\n")
    terminal.symlink_to(target)
    assert invoke().exit_code == 1
    assert target.read_text() == "REVERB_API_TOKEN=kept\n"
    assert terminal.is_symlink()


def test_concurrent_edit_is_not_overwritten(terminal):
    previous = b"VISION_MODEL=original\n"
    terminal.write_bytes(b"VISION_MODEL=changed\n")
    with pytest.raises(ValueError):
        save_openai_key(terminal, previous, INERT)
    assert terminal.read_bytes() == b"VISION_MODEL=changed\n"
    assert not list(terminal.parent.glob(".env-*"))


def test_noninteractive_input_cannot_fall_back_to_echo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = invoke()
    assert result.exit_code == 1
    assert not (tmp_path / ".env").exists()


def test_setup_journey_and_missing_key_analysis_preserve_draft(application, draft):
    web = create_app(application.settings)
    client = TestClient(web, base_url="http://127.0.0.1:8765")
    assert client.get("/setup/openai").status_code == 403
    client.get(f"/unlock?key={web.state.unlock}")
    for path in ("/", f"/drafts/{draft.id}"):
        assert 'href="/setup/openai"' in client.get(path).text
    response = client.post(
        f"/drafts/{draft.id}/analyze",
        data={"csrf": web.state.csrf, "revision": draft.revision},
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    assert response.status_code == 400
    assert 'href="/setup/openai"' in response.text
    assert application.store.load(draft.id) == draft
    stale = client.post(
        f"/drafts/{draft.id}/analyze",
        data={"csrf": web.state.csrf, "revision": draft.revision + 1},
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    for unrelated, status in ((stale, 409), (client.get("/drafts/missing"), 404)):
        assert unrelated.status_code == status
        assert 'href="/setup/openai"' not in unrelated.text
    guidance = client.get("/setup/openai")
    assert guidance.status_code == 200
    assert "synthshop setup-openai" in guidance.text
    assert "<input" not in guidance.text
    assert client.post(
        "/setup/openai", data={"csrf": web.state.csrf},
        headers={"Origin": "http://127.0.0.1:8765"}
    ).status_code == 405

def test_configured_web_never_exposes_key(application):
    application.settings.openai_api_key = INERT
    web = create_app(application.settings)
    client = TestClient(web, base_url="http://127.0.0.1:8765")
    client.get(f"/unlock?key={web.state.unlock}")
    for path in ("/", "/setup/openai"):
        response = client.get(path)
        assert INERT not in response.text
        assert INERT not in str(response.headers)
    assert "Key configured in this running app" in client.get("/setup/openai").text
