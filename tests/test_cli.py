"""Loopback HTTP approval/security contracts and removed implicit publishing CLI."""

import re

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from synthshop.cli.main import app
from synthshop.web.app import create_app

LOCAL = "http://127.0.0.1:8765"


def browser(application):
    web = create_app(application.settings)
    client = TestClient(web, base_url=LOCAL)
    assert client.get(f"/unlock?key={web.state.unlock}").status_code == 200
    return client, web.state.csrf


@pytest.mark.parametrize("command", [["publish", "--live"], ["list"]])
def test_removed_cli_commands(command):
    result = CliRunner().invoke(app, command)
    assert result.exit_code != 0


def test_launcher_link_unlocks_one_browser_once(tmp_path, monkeypatch):
    launched = {}
    monkeypatch.setenv("PRODUCTS_DIR", str(tmp_path / "legacy"))
    monkeypatch.setattr(
        "synthshop.cli.main.uvicorn.run", lambda web, **options: launched.update(web=web, **options)
    )
    result = CliRunner().invoke(app, ["serve", "--no-open", "--data-dir", str(tmp_path / "data")])
    assert result.exit_code == 0
    assert launched["host"] == "127.0.0.1"
    assert launched["access_log"] is False
    link = re.search(rf"{LOCAL}(/unlock\?key=\S+)", result.output).group(1)
    owner = TestClient(launched["web"], base_url=LOCAL)
    assert owner.get("/").status_code == 403
    unlocked = owner.get(link)
    assert unlocked.status_code == 200
    assert str(unlocked.url) == LOCAL + "/"
    assert "Saved drafts" in unlocked.text
    assert owner.get(link).status_code == 200
    other = TestClient(launched["web"], base_url=LOCAL)
    assert other.get(link).status_code == 403
    assert "synthshop_session" not in other.cookies
    assert other.get("/").status_code == 403


def test_locked_server_exposes_nothing_without_launch_link(application, draft):
    web = create_app(application.settings)
    stranger = TestClient(web, base_url=LOCAL)
    for path in ("/", f"/drafts/{draft.id}", f"/photos/{draft.photos[0].id}", "/unlock?key=guess"):
        response = stranger.get(path)
        assert response.status_code == 403
        assert "set-cookie" not in response.headers
        assert draft.title not in response.text
    response = stranger.post(
        f"/drafts/{draft.id}/review",
        data={"csrf": web.state.csrf, "revision": draft.revision},
        headers={"Origin": LOCAL},
    )
    assert response.status_code == 403
    assert application.store.attempt(draft.id) is None


def test_malformed_shipping_amount_is_a_field_error(application, draft):
    client, csrf = browser(application)
    for rates, message in (("CA=fifty", "Shipping rate CA"), ("CA", "CODE=amount")):
        response = client.post(
            f"/drafts/{draft.id}/save",
            data={
                "csrf": csrf,
                "revision": draft.revision,
                "price": "190",
                "international_rates": rates,
            },
            headers={"Origin": LOCAL},
        )
        assert response.status_code == 400
        assert message in response.text
    assert application.store.load(draft.id).revision == draft.revision


def test_cross_site_and_untrusted_host_cannot_upload(application, image_bytes):
    client, csrf = browser(application)
    response = client.post(
        "/drafts",
        data={"csrf": csrf},
        files={"photos": ("fake.png", image_bytes)},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 403
    assert application.store.list_all() == []


def test_csrf_required_even_with_same_origin(application, image_bytes):
    client, _csrf = browser(application)
    response = client.post(
        "/drafts",
        data={"csrf": "guessed"},
        files={"photos": ("fake.png", image_bytes)},
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    assert response.status_code == 403
    assert application.store.list_all() == []


def test_upload_is_local_persistent_and_get_cannot_publish(application, image_bytes):
    client, csrf = browser(application)
    response = client.post(
        "/drafts",
        data={"csrf": csrf},
        files={"photos": ("misnamed.png", image_bytes, "image/png")},
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    assert response.status_code == 200
    item = application.store.list_all()[0]
    assert item.photos[0].original_format == "WEBP"
    assert application.store.attempt(item.id) is None
    assert client.get(f"/drafts/{item.id}/publish").status_code == 405
    restarted, _ = browser(application)
    assert restarted.get(f"/drafts/{item.id}").status_code == 200
    assert restarted.get(f"/photos/{item.photos[0].id}").headers["content-type"] == "image/jpeg"


def test_missing_explicit_approval_cannot_publish(application, draft):
    client, csrf = browser(application)
    response = client.post(
        f"/drafts/{draft.id}/publish",
        data={"csrf": csrf, "revision": draft.revision},
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    assert response.status_code == 400
    assert application.store.attempt(draft.id) is None


def test_imported_text_is_escaped_not_executable(application, draft):
    changed = application.edit(
        draft.id,
        draft.revision,
        {
            "title": '<script>alert("bad")</script>',
            "price": "190",
        },
    )
    client, _ = browser(application)
    response = client.get(f"/drafts/{changed.id}")
    assert '<script>alert("bad")</script>' not in response.text
    assert "&lt;script&gt;" in response.text
    assert "script-src 'none'" in response.headers["content-security-policy"]
