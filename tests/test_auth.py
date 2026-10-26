from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_signup_and_login_flow():
    email = "developer@example.com"
    password = "secretpassword123"

    # Signup
    signup_resp = client.post("/auth/signup", json={"email": email, "password": password})
    assert signup_resp.status_code in (201, 400)
    if signup_resp.status_code == 201:
        data = signup_resp.json()
        assert "token" in data
        assert data["user"]["email"] == email

    # Login
    login_resp = client.post("/auth/login", json={"email": email, "password": password})
    assert login_resp.status_code == 200
    token = login_resp.json()["token"]
    assert token

    # Check /auth/me
    me_resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == email

    # Incorrect login
    bad_login = client.post("/auth/login", json={"email": email, "password": "wrongpassword"})
    assert bad_login.status_code == 401
