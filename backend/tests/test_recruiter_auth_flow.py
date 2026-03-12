"""Recruiter auth API regression tests for signup/login/me and remember-me token behavior."""

import os
import time
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv
from jose import jwt


load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")


BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")
if not BASE_URL:
    pytest.skip("REACT_APP_BACKEND_URL is not configured", allow_module_level=True)

API_BASE = f"{BASE_URL.rstrip('/')}/api"


@pytest.fixture(scope="session")
def api_client():
    return requests.Session()


def _unique_email() -> str:
    return f"test_recruiter_{uuid.uuid4().hex[:10]}@example.com"


def _signup_recruiter(api_client, email: str, password: str = "SecurePass@123"):
    return api_client.post(
        f"{API_BASE}/auth/recruiters/signup",
        json={
            "name": "TEST Recruiter",
            "email": email,
            "company": "TEST Company",
            "role": "TEST Hiring Manager",
            "password": password,
            "confirm_password": password,
        },
        timeout=60,
    )


def test_signup_creates_recruiter_and_returns_bearer_token(api_client):
    email = _unique_email()
    response = _signup_recruiter(api_client, email)
    assert response.status_code == 200

    payload = response.json()
    assert payload["token_type"] == "bearer"
    assert isinstance(payload["access_token"], str) and len(payload["access_token"]) > 20
    assert payload["recruiter"]["email"] == email
    assert payload["recruiter"]["name"] == "TEST Recruiter"
    assert payload["recruiter"]["company"] == "TEST Company"


def test_login_validates_credentials_and_returns_token(api_client):
    email = _unique_email()
    password = "SecurePass@123"
    signup_response = _signup_recruiter(api_client, email, password=password)
    assert signup_response.status_code == 200

    login_response = api_client.post(
        f"{API_BASE}/auth/recruiters/login",
        json={"email": email, "password": password, "remember_me": True},
        timeout=60,
    )
    assert login_response.status_code == 200

    data = login_response.json()
    assert data["token_type"] == "bearer"
    assert data["recruiter"]["email"] == email
    assert isinstance(data["access_token"], str) and len(data["access_token"]) > 20


def test_login_rejects_invalid_password(api_client):
    email = _unique_email()
    signup_response = _signup_recruiter(api_client, email, password="SecurePass@123")
    assert signup_response.status_code == 200

    login_response = api_client.post(
        f"{API_BASE}/auth/recruiters/login",
        json={"email": email, "password": "WrongPass@123", "remember_me": True},
        timeout=60,
    )
    assert login_response.status_code == 401
    assert "detail" in login_response.json()


def test_recruiter_me_requires_bearer_token(api_client):
    response = api_client.get(f"{API_BASE}/auth/recruiters/me", timeout=60)
    assert response.status_code == 401
    assert "detail" in response.json()


def test_recruiter_me_returns_profile_for_valid_token(api_client):
    email = _unique_email()
    password = "SecurePass@123"
    signup_response = _signup_recruiter(api_client, email, password=password)
    assert signup_response.status_code == 200
    token = signup_response.json()["access_token"]

    me_response = api_client.get(
        f"{API_BASE}/auth/recruiters/me",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    assert me_response.status_code == 200
    me_payload = me_response.json()
    assert me_payload["email"] == email
    assert me_payload["name"] == "TEST Recruiter"
    assert me_payload["company"] == "TEST Company"


def test_remember_me_true_has_longer_exp_than_false(api_client):
    email = _unique_email()
    password = "SecurePass@123"
    signup_response = _signup_recruiter(api_client, email, password=password)
    assert signup_response.status_code == 200

    short_response = api_client.post(
        f"{API_BASE}/auth/recruiters/login",
        json={"email": email, "password": password, "remember_me": False},
        timeout=60,
    )
    assert short_response.status_code == 200

    time.sleep(1)
    long_response = api_client.post(
        f"{API_BASE}/auth/recruiters/login",
        json={"email": email, "password": password, "remember_me": True},
        timeout=60,
    )
    assert long_response.status_code == 200

    short_claims = jwt.get_unverified_claims(short_response.json()["access_token"])
    long_claims = jwt.get_unverified_claims(long_response.json()["access_token"])
    assert long_claims["exp"] > short_claims["exp"]