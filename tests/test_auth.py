"""Auth, sessions, profile and ownership. Uses a throwaway database; nothing touches real data."""
import dataclasses

import pytest
from fastapi.testclient import TestClient

from app import auth, main
from app.capture.schema import AuditReport
from app.capture.store import AuditStore

GOOD = "correct-horse-battery-9"
H = {"X-Requested-With": "pardarshi"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    s = dataclasses.replace(auth.settings, data_dir=tmp_path)
    monkeypatch.setattr(auth, "settings", s)
    monkeypatch.setattr(auth, "_conn", None)
    store = AuditStore(tmp_path / "runs")
    monkeypatch.setattr(main, "store", store)
    auth.limiter.hits.clear()
    with TestClient(main.app, headers=H) as c:
        c.store = store
        yield c


def signup(c, email="ana@example.com", name="Ana Rao", pw=GOOD):
    return c.post("/api/auth/signup", json={"name": name, "email": email, "password": pw})


def test_signup_sets_httponly_cookie_and_me_works(client):
    r = signup(client)
    assert r.status_code == 200 and r.json()["initials"] == "AR" and r.json()["onboarded"] is False
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=lax" in r.headers["set-cookie"].lower()
    assert client.get("/api/auth/me").json()["email"] == "ana@example.com"


@pytest.mark.parametrize("pw,hint", [("short1", "10 characters"), ("password123", "easy to guess"), ("onlyletterspassword", "number or symbol"),
                                     ("ana-rao-2026!", "email")])
def test_weak_passwords_rejected(client, pw, hint):
    r = signup(client, email="anarao@example.com" if "email" in hint else "ana@example.com", pw=pw)
    assert r.status_code == 422 and hint in r.json()["detail"]


def test_duplicate_and_invalid_email(client):
    assert signup(client).status_code == 200
    assert signup(client).status_code == 409
    assert signup(client, email="not-an-email").status_code == 422


def test_login_logout_and_generic_error(client):
    signup(client)
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401
    bad_pw = client.post("/api/auth/login", json={"email": "ana@example.com", "password": "wrong-password-1"})
    no_user = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "wrong-password-1"})
    assert bad_pw.status_code == no_user.status_code == 401 and bad_pw.json() == no_user.json()  # no user enumeration
    ok = client.post("/api/auth/login", json={"email": " ANA@example.com ", "password": GOOD})
    assert ok.status_code == 200 and client.get("/api/auth/me").status_code == 200


def test_lockout_after_repeated_failures(client):
    signup(client)
    client.post("/api/auth/logout")
    codes = [client.post("/api/auth/login", json={"email": "ana@example.com", "password": f"wrong-pass-{i}9"}).status_code for i in range(8)]
    assert codes[:6] == [401] * 6 and codes[6] == 429
    assert client.post("/api/auth/login", json={"email": "ana@example.com", "password": GOOD}).status_code == 429  # even the right one


def test_csrf_header_and_origin_enforced(client):
    r = client.post("/api/auth/signup", json={"name": "X", "email": "x@example.com", "password": GOOD}, headers={"X-Requested-With": ""})
    assert r.status_code == 403
    r = client.post("/api/auth/signup", json={"name": "X", "email": "x@example.com", "password": GOOD}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_security_headers_present(client):
    r = client.get("/api/health")
    assert r.headers["x-frame-options"] == "DENY" and "default-src 'self'" in r.headers["content-security-policy"]
    # the voice assistant needs the microphone for this origin only: a blanket "microphone=()" would silently break it
    assert "microphone=(self)" in r.headers["permissions-policy"] and "camera=()" in r.headers["permissions-policy"]


def test_api_requires_auth(client):
    for path in ("/api/audits", "/api/sites", "/api/profile/stats", "/api/classifier", "/api/health/details"):
        assert client.get(path).status_code == 401, path
    assert client.get("/api/health").status_code == 200


def test_profile_update_validation(client):
    signup(client)
    ok = client.patch("/api/profile", json={"country": "IN", "city": "Delhi", "use_cases": ["travel", "bogus", "tickets"], "theme": "dark", "onboarded": True})
    assert ok.status_code == 200 and ok.json()["use_cases"] == ["travel", "tickets"] and ok.json()["onboarded"] is True
    assert client.patch("/api/profile", json={"country": "ZZ"}).status_code == 422
    assert client.patch("/api/profile", json={"theme": "neon"}).status_code == 422
    assert client.patch("/api/profile", json={"name": "   "}).status_code == 422


def test_password_change_signs_out_other_devices(client):
    signup(client)
    other = TestClient(main.app, headers=H)
    assert other.post("/api/auth/login", json={"email": "ana@example.com", "password": GOOD}).status_code == 200
    assert len(client.get("/api/profile/sessions").json()) == 2
    bad = client.post("/api/profile/password", json={"current": "nope-nope-nope1", "new": "another-good-pass-7"})
    assert bad.status_code == 403
    assert client.post("/api/profile/password", json={"current": GOOD, "new": "another-good-pass-7"}).status_code == 200
    assert other.get("/api/auth/me").status_code == 401 and client.get("/api/auth/me").status_code == 200
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "ana@example.com", "password": "another-good-pass-7"}).status_code == 200


def _fake_audit(c, audit_id, owner_id):
    c.store.save_report(AuditReport(audit_id=audit_id, site="Secret Shop", provider="opensource", trust_score=40))
    (c.store.shots_dir(audit_id) / "a.jpg").write_bytes(b"jpegbytes")
    auth.set_owner(audit_id, owner_id)


def test_audits_are_private_to_their_owner(client):
    a = signup(client, "a@example.com").json()
    _fake_audit(client, "secret-1", a["id"])
    assert client.get("/api/audits/secret-1").status_code == 200
    assert client.get("/files/secret-1/shots/a.jpg").status_code == 200
    other = TestClient(main.app, headers=H)
    other.post("/api/auth/signup", json={"name": "Bo", "email": "b@example.com", "password": GOOD})
    assert other.get("/api/audits/secret-1").status_code == 404
    assert other.get("/files/secret-1/shots/a.jpg").status_code == 404
    assert other.delete("/api/audits/secret-1").status_code == 404
    assert "secret-1" not in [x["audit_id"] for x in other.get("/api/audits").json()]
    assert other.get("/files/../../etc/passwd").status_code in (404, 400)
    assert other.get("/api/audits/secret-1/dossier").status_code == 404


def test_shared_samples_visible_and_not_deletable(client):
    signup(client)
    client.store.save_report(AuditReport(audit_id="sample-1", site="Sample", provider="opensource", trust_score=90))
    assert "sample-1" in [x["audit_id"] for x in client.get("/api/audits").json()]
    assert client.delete("/api/audits/sample-1").status_code == 404


def test_guest_session_is_limited(client):
    g = client.post("/api/auth/guest").json()
    assert g["is_guest"] and g["email"] is None and g["onboarded"]
    assert client.post("/api/profile/password", json={"current": "x", "new": GOOD}).status_code == 400


def test_guest_upgrade_keeps_audits(client):
    g = client.post("/api/auth/guest").json()
    _fake_audit(client, "guest-audit", g["id"])
    assert client.get("/", follow_redirects=False).status_code == 307  # guests are sent to the console...
    assert client.get("/?upgrade=1", follow_redirects=False).status_code == 200  # ...unless they are upgrading
    u = signup(client, "up@example.com").json()
    assert auth.owner_of("guest-audit") == u["id"] and client.get("/api/audits/guest-audit").status_code == 200
    assert auth.q("SELECT 1 FROM users WHERE id=?", (g["id"],)).fetchone() is None


def test_delete_account_removes_data(client):
    u = signup(client).json()
    _fake_audit(client, "mine-1", u["id"])
    assert client.request("DELETE", "/api/profile", json={"password": "wrong-password-1"}).status_code == 403
    assert client.request("DELETE", "/api/profile", json={"password": GOOD}).status_code == 200
    assert not (client.store.root / "mine-1").exists()
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login", json={"email": "ana@example.com", "password": GOOD}).status_code == 401


def test_pages_redirect_when_signed_out(client):
    assert client.get("/app", follow_redirects=False).status_code == 307
    assert client.get("/profile", follow_redirects=False).status_code == 307
    assert client.get("/report?audit=x", follow_redirects=False).status_code == 307


def test_ask_endpoint_answers_from_the_audit_and_is_owner_only(client):
    a = signup(client, "a@example.com").json()
    _fake_audit(client, "ask-1", a["id"])
    r = client.post("/api/audits/ask-1/ask", json={"question": "Give me a summary"})
    assert r.status_code == 200 and "40 out of 100" in r.json()["text"] and r.json()["method"] == "local"
    assert client.post("/api/audits/ask-1/ask", json={"question": ""}).status_code == 422
    other = TestClient(main.app, headers=H)
    other.post("/api/auth/signup", json={"name": "Bo", "email": "b@example.com", "password": GOOD})
    assert other.post("/api/audits/ask-1/ask", json={"question": "summary"}).status_code == 404
    assert TestClient(main.app, headers=H).post("/api/audits/ask-1/ask", json={"question": "summary"}).status_code == 401


def test_dossier_fingerprints_report_and_cited_screenshots(client):
    from app.capture.schema import Evidence, Finding
    a = signup(client, "d@example.com").json()
    rep = AuditReport(audit_id="dos-1", site="Shop", provider="opensource", trust_score=40, findings=[
        Finding(id="drip_pricing", type="drip_pricing", title="t", detail="d",
                evidence=[Evidence(step_name="event", screenshot="dos-1/shots/a.jpg")])])
    client.store.save_report(rep)
    (client.store.shots_dir("dos-1") / "a.jpg").write_bytes(b"jpegbytes")
    auth.set_owner("dos-1", a["id"])
    d = client.get("/api/audits/dos-1/dossier").json()
    assert d["reference"].startswith("PDR-") and len(d["fingerprint"]) == 64
    assert [f["path"] for f in d["files"]] == ["report.json", "shots/a.jpg"]
    assert all(f["sha256"] for f in d["files"])
    assert client.get("/api/audits/dos-1/dossier").json()["fingerprint"] == d["fingerprint"]  # stable
    (client.store.shots_dir("dos-1") / "a.jpg").write_bytes(b"tampered")
    assert client.get("/api/audits/dos-1/dossier").json()["fingerprint"] != d["fingerprint"]  # detects change


def test_own_account_sessions_are_private_and_checked(client, monkeypatch):
    from app import sessions
    monkeypatch.setattr(sessions, "settings", auth.settings)  # the fixture's temporary data dir
    monkeypatch.setattr(main, "ensure_allowed", lambda url: None)
    body = {"site": "shop.example.com", "use_session": True, "provider": "opensource"}

    guest = TestClient(main.app, headers=H)
    guest.post("/api/auth/guest")
    assert "Create an account" in guest.post("/api/audits", json=body).json()["detail"]

    a = signup(client, "s@example.com").json()
    r = client.post("/api/audits", json=body)
    assert r.status_code == 400 and "No saved sign-in for shop.example.com" in r.json()["detail"]
    assert "demo shops" in client.post("/api/audits", json={**body, "site": "dirty"}).json()["detail"]
    assert "Open source provider only" in client.post("/api/audits", json={**body, "provider": "tinyfish"}).json()["detail"]

    p = sessions.path_for(a["id"], "shop.example.com")
    p.parent.mkdir(parents=True)
    p.write_text("{}")
    assert [s["host"] for s in client.get("/api/sessions").json()] == ["shop.example.com"]
    assert sessions.find(a["id"], "www.shop.example.com") == p  # with or without www.

    other = TestClient(main.app, headers=H)
    other.post("/api/auth/signup", json={"name": "Bo", "email": "o@example.com", "password": GOOD})
    assert other.get("/api/sessions").json() == []  # never another user's sign-in
    assert other.post("/api/audits", json=body).status_code == 400

    assert client.delete("/api/sessions/shop.example.com").json() == {"ok": True} and not p.exists()
    p.write_text("{}")
    assert client.request("DELETE", "/api/profile", json={"password": GOOD}).status_code == 200
    assert not p.parent.exists()  # deleting the account deletes its saved sign-ins
    assert sessions.clean_host("https://Shop.Example.com/cart") == "shop.example.com"
    with pytest.raises(ValueError):
        sessions.clean_host("../../etc")


def test_site_list_includes_allowlisted_real_sites_once(client, monkeypatch):
    s = dataclasses.replace(main.settings, allowed_hosts=("127.0.0.1", "localhost", "www.shop.in", "shop.in", "store.com"))
    monkeypatch.setattr(main, "settings", s)
    signup(client)
    got = client.get("/api/sites").json()
    assert [x["id"] for x in got if x["kind"] == "demo"] == ["dirty", "clean"]
    assert [(x["id"], x["label"]) for x in got if x["kind"] == "allowed"] == [("www.shop.in", "shop.in"), ("store.com", "store.com")]


def test_connect_window_is_guarded(client, monkeypatch):
    from app import connect
    opened = []
    monkeypatch.setattr(connect, "start", lambda uid, host: opened.append((uid, host)) or {"host": host, "browser": "chrome"})
    guest = TestClient(main.app, headers=H)
    guest.post("/api/auth/guest")
    assert "Create an account" in guest.post("/api/sessions/www.flipkart.com/connect").json()["detail"]
    a = signup(client, "c@example.com").json()
    monkeypatch.setattr(main, "ensure_allowed", lambda url: (_ for _ in ()).throw(PermissionError("not on the allowlist")))
    assert "allowlist" in client.post("/api/sessions/evil.example.com/connect").json()["detail"]
    monkeypatch.setattr(main, "ensure_allowed", lambda url: None)
    assert client.post("/api/sessions/www.flipkart.com/connect").json()["browser"] == "chrome"
    assert opened == [(a["id"], "www.flipkart.com")]
    assert "sign-in window is closed" in client.post("/api/sessions/www.flipkart.com/save").json()["detail"]
    r = TestClient(main.app).post("/api/sessions/www.flipkart.com/connect")  # no X-Requested-With header
    assert r.status_code == 403
