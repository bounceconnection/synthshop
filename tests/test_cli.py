"""Loopback HTTP approval/security contracts and removed implicit publishing CLI."""

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from synthshop.cli.main import app
from synthshop.web.app import create_app


def browser(application):
    web = create_app(application.settings)
    client = TestClient(web, base_url="http://127.0.0.1:8765")
    client.get("/")
    return client, web.state.csrf


def test_removed_cli_publish_shortcut():
    result = CliRunner().invoke(app, ["publish", "--live"])
    assert result.exit_code != 0


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
