"""API tests for resume screener core endpoints and export workflow."""

"""Smart portfolio verifier + link scanning + export regression coverage."""

import csv
import io
import os
import time
import uuid
import zipfile
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")


BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
API_BASE = f"{BASE_URL}/api"


@pytest.fixture(scope="session")
def api_client():
    session = requests.Session()
    return session


def _build_resume_zip() -> bytes:
    resume_1 = """
    Alice Johnson
    Python, FastAPI, Docker, AWS, React
    5 years of experience in backend engineering
    Bachelor's degree in Computer Science
    https://github.com/octocat
    https://linkedin.com/in/alice-johnson
    https://www.linkedin.com/in/alice-johnson-alt
    """

    resume_2 = """
    Bob Smith
    Java, Spring, SQL
    2 years experience in software development
    Diploma in Information Technology
    https://example-portfolio.dev/bob
    https://bobsmith.dev
    """

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("alice_johnson.txt", resume_1)
        zf.writestr("bob_smith.txt", resume_2)
    buffer.seek(0)
    return buffer.getvalue()


def _candidate_by_source_file(results, source_file_name):
    return next((item for item in results if item.get("source_file") == source_file_name), None)


def _build_handle_only_resume_zip() -> bytes:
    resume = """
    Aisac Jose
    Portfolio LinkedIn GitHub
    GitHub Link: Aisac-Jose-k
    LinkedIn: Aisac-Jose-k
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("aisac_resume.txt", resume)
    buffer.seek(0)
    return buffer.getvalue()


def _build_minimal_resume_zip() -> bytes:
    resume = """
    Demo Candidate
    Python FastAPI backend developer with 3 years of experience.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("demo_candidate.txt", resume)
    buffer.seek(0)
    return buffer.getvalue()


def _build_large_resume_zip(count: int = 20) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for index in range(count):
            resume = f"""
            Candidate {index}
            Python FastAPI React AWS Docker
            {2 + (index % 5)} years of experience
            Bachelor's degree
            https://github.com/octocat
            https://www.linkedin.com/in/octocat
            """
            zf.writestr(f"candidate_{index}.txt", resume)
    buffer.seek(0)
    return buffer.getvalue()


def test_analyze_resumes_success_and_response_shape(api_client):
    jd_text = (
        "Hiring Python FastAPI engineer with React and AWS experience. "
        "Need 3 years experience and bachelor degree."
    )
    zip_bytes = _build_resume_zip()

    files = {
        "resumes_zip": ("resumes.zip", zip_bytes, "application/zip"),
    }
    data = {"jd_text": jd_text}

    response = api_client.post(f"{API_BASE}/screener/analyze", data=data, files=files, timeout=60)
    assert response.status_code == 200

    payload = response.json()
    assert isinstance(payload.get("batch_id"), str)
    assert payload["analytics"]["resumes_uploaded"] == 2
    assert len(payload["results"]) == 2
    assert payload["results"][0]["fit_score"] >= payload["results"][1]["fit_score"]
    assert "verified_links" in payload["results"][0]
    assert "github" in payload["results"][0]["verified_links"]
    assert "github_analysis" in payload["results"][0]["verified_links"]
    assert "linkedin_analysis" in payload["results"][0]["verified_links"]
    assert "smart_portfolio" in payload["results"][0]["verified_links"]

    all_scanned_links = [
        link
        for candidate in payload["results"]
        for link in candidate["verified_links"].get("scanned_links", [])
    ]
    assert len(all_scanned_links) >= 3
    assert any(link.get("link_type") == "linkedin" for link in all_scanned_links)


def test_smart_portfolio_contract_fields_present(api_client):
    jd_text = "Hiring Python FastAPI engineer with React and AWS experience."
    zip_bytes = _build_resume_zip()

    response = api_client.post(
        f"{API_BASE}/screener/analyze",
        data={"jd_text": jd_text},
        files={"resumes_zip": ("resumes.zip", zip_bytes, "application/zip")},
        timeout=60,
    )
    assert response.status_code == 200

    payload = response.json()
    alice = _candidate_by_source_file(payload["results"], "alice_johnson.txt")
    assert alice is not None

    verified_links = alice["verified_links"]
    github_analysis = verified_links["github_analysis"]
    linkedin_analysis = verified_links["linkedin_analysis"]
    smart_portfolio = verified_links["smart_portfolio"]

    assert isinstance(github_analysis.get("verified"), bool)
    assert isinstance(github_analysis.get("top_projects", []), list)
    assert isinstance(linkedin_analysis.get("verified"), bool)
    assert isinstance(linkedin_analysis.get("verification_score"), (int, float))
    assert isinstance(smart_portfolio.get("verification_bonus"), (int, float))
    assert isinstance(smart_portfolio.get("stack_experience_verified"), bool)
    assert isinstance(smart_portfolio.get("hr_insight"), str)

    top_projects = github_analysis.get("top_projects", [])
    if top_projects:
        top_project = top_projects[0]
        assert "project_type" in top_project
        assert "complexity_score" in top_project
        assert "jd_stack_coverage_pct" in top_project


def test_analyze_scans_all_resume_links_for_each_candidate(api_client):
    jd_text = "Python FastAPI role with AWS and React."
    zip_bytes = _build_resume_zip()

    response = api_client.post(
        f"{API_BASE}/screener/analyze",
        data={"jd_text": jd_text},
        files={"resumes_zip": ("resumes.zip", zip_bytes, "application/zip")},
        timeout=60,
    )
    assert response.status_code == 200

    payload = response.json()
    alice = _candidate_by_source_file(payload["results"], "alice_johnson.txt")
    bob = _candidate_by_source_file(payload["results"], "bob_smith.txt")
    assert alice is not None
    assert bob is not None

    alice_scanned = alice["verified_links"].get("scanned_links", [])
    bob_scanned = bob["verified_links"].get("scanned_links", [])
    assert len(alice_scanned) == 3
    assert len(bob_scanned) == 2
    assert len(alice_scanned) + len(bob_scanned) == 5


# Link scanning coverage: verify all URLs are scanned and metadata is returned per link
def test_analyze_scans_all_links_and_returns_per_link_metadata(api_client):
    jd_text = "Python FastAPI role with AWS and React."
    zip_bytes = _build_resume_zip()

    response = api_client.post(
        f"{API_BASE}/screener/analyze",
        data={"jd_text": jd_text},
        files={"resumes_zip": ("resumes.zip", zip_bytes, "application/zip")},
        timeout=60,
    )
    assert response.status_code == 200

    payload = response.json()
    assert len(payload["results"]) == 2

    alice = _candidate_by_source_file(payload["results"], "alice_johnson.txt")
    assert alice is not None

    # Alice resume has exactly 3 URLs in the fixture: 1 GitHub + 2 LinkedIn
    scanned_links = alice["verified_links"].get("scanned_links", [])
    assert len(scanned_links) == 3

    for link_item in scanned_links:
        assert isinstance(link_item.get("url"), str)
        assert link_item.get("link_type") in {"github", "linkedin", "portfolio", "other"}
        assert isinstance(link_item.get("reachable"), bool)
        assert isinstance(link_item.get("valid_format"), bool)
        assert "status_code" in link_item
        assert "notes" in link_item

    smart_summary = alice["verified_links"].get("smart_portfolio", {})
    assert "verification_bonus" in smart_summary
    assert smart_summary.get("verification_bonus", 0) >= 0

    github_analysis = alice["verified_links"].get("github_analysis", {})
    assert "top_projects" in github_analysis
    assert isinstance(github_analysis.get("top_projects", []), list)


# LinkedIn multi-link coverage: verify multiple LinkedIn links are detected and surfaced
def test_analyze_detects_multiple_linkedin_links(api_client):
    jd_text = "Need backend engineer with Python"
    zip_bytes = _build_resume_zip()

    response = api_client.post(
        f"{API_BASE}/screener/analyze",
        data={"jd_text": jd_text},
        files={"resumes_zip": ("resumes.zip", zip_bytes, "application/zip")},
        timeout=60,
    )
    assert response.status_code == 200

    payload = response.json()
    alice = _candidate_by_source_file(payload["results"], "alice_johnson.txt")
    assert alice is not None

    linkedin_urls = alice["verified_links"].get("linkedin_urls", [])
    assert len(linkedin_urls) == 2
    assert all("linkedin.com" in url for url in linkedin_urls)

    scanned_linkedin = [
        item for item in alice["verified_links"].get("scanned_links", []) if item.get("link_type") == "linkedin"
    ]
    assert len(scanned_linkedin) == 2


def test_analyze_infers_github_and_linkedin_from_handle_labels(api_client):
    jd_text = "Need python backend engineer"
    zip_bytes = _build_handle_only_resume_zip()

    response = api_client.post(
        f"{API_BASE}/screener/analyze",
        data={"jd_text": jd_text},
        files={"resumes_zip": ("resumes.zip", zip_bytes, "application/zip")},
        timeout=60,
    )
    assert response.status_code == 200

    payload = response.json()
    candidate = _candidate_by_source_file(payload["results"], "aisac_resume.txt")
    assert candidate is not None

    github_urls = candidate["verified_links"].get("github_urls", [])
    linkedin_urls = candidate["verified_links"].get("linkedin_urls", [])
    assert "https://github.com/Aisac-Jose-k" in github_urls
    assert "https://www.linkedin.com/in/Aisac-Jose-k" in linkedin_urls


def test_get_results_and_analytics_and_export_csv(api_client):
    jd_text = "Python developer required with docker and aws skills. 2 years experience."
    zip_bytes = _build_resume_zip()

    create_resp = api_client.post(
        f"{API_BASE}/screener/analyze",
        data={"jd_text": jd_text},
        files={"resumes_zip": ("candidates.zip", zip_bytes, "application/zip")},
        timeout=60,
    )
    assert create_resp.status_code == 200
    batch_id = create_resp.json()["batch_id"]

    results_resp = api_client.get(f"{API_BASE}/screener/results/{batch_id}", timeout=30)
    assert results_resp.status_code == 200
    results_data = results_resp.json()
    assert results_data["batch_id"] == batch_id
    assert len(results_data["results"]) == 2

    analytics_resp = api_client.get(f"{API_BASE}/screener/analytics/{batch_id}", timeout=30)
    assert analytics_resp.status_code == 200
    analytics_data = analytics_resp.json()
    assert analytics_data["resumes_uploaded"] == 2
    assert "score_distribution" in analytics_data

    export_resp = api_client.get(f"{API_BASE}/screener/export/{batch_id}", timeout=30)
    assert export_resp.status_code == 200
    assert "text/csv" in export_resp.headers.get("content-type", "")
    assert "candidate_name" in export_resp.text
    assert "fit_score" in export_resp.text

    reader = csv.DictReader(io.StringIO(export_resp.text))
    rows = list(reader)
    assert len(rows) == 2
    assert "smart_verification_bonus" in reader.fieldnames
    assert "stack_coverage_pct" in reader.fieldnames
    assert "jd_relevant_projects" in reader.fieldnames
    assert "best_project_complexity" in reader.fieldnames


def test_async_start_and_status_flow(api_client):
    zip_bytes = _build_minimal_resume_zip()
    start_resp = api_client.post(
        f"{API_BASE}/screener/analyze/start",
        data={"jd_text": "Need Python FastAPI engineer"},
        files={"resumes_zip": ("resumes.zip", zip_bytes, "application/zip")},
        timeout=60,
    )
    assert start_resp.status_code == 200
    start_data = start_resp.json()
    batch_id = start_data["batch_id"]
    assert start_data["status"] == "processing"

    final_status = None
    for _ in range(20):
        status_resp = api_client.get(f"{API_BASE}/screener/status/{batch_id}", timeout=30)
        assert status_resp.status_code == 200
        final_status = status_resp.json()
        if final_status.get("status") in {"completed", "failed"}:
            break
        time.sleep(1)

    assert final_status is not None
    assert final_status.get("status") == "completed"

    results_resp = api_client.get(f"{API_BASE}/screener/results/{batch_id}", timeout=60)
    assert results_resp.status_code == 200


def test_async_start_returns_quickly_with_batch_id(api_client):
    zip_bytes = _build_minimal_resume_zip()
    start_time = time.time()
    response = api_client.post(
        f"{API_BASE}/screener/analyze/start",
        data={"jd_text": "Need Python FastAPI engineer"},
        files={"resumes_zip": ("resumes.zip", zip_bytes, "application/zip")},
        timeout=60,
    )
    elapsed = time.time() - start_time

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload.get("batch_id"), str)
    assert payload.get("status") == "processing"
    assert elapsed < 8


def test_results_export_analytics_behavior_during_processing_then_completion(api_client):
    zip_bytes = _build_large_resume_zip(count=20)
    start_resp = api_client.post(
        f"{API_BASE}/screener/analyze/start",
        data={"jd_text": "Need Python FastAPI engineer with React and AWS experience"},
        files={"resumes_zip": ("bulk_resumes.zip", zip_bytes, "application/zip")},
        timeout=60,
    )
    assert start_resp.status_code == 200
    batch_id = start_resp.json()["batch_id"]

    observed_processing = False
    for _ in range(8):
        status_resp = api_client.get(f"{API_BASE}/screener/status/{batch_id}", timeout=30)
        assert status_resp.status_code == 200
        status_payload = status_resp.json()
        if status_payload.get("status") == "processing":
            observed_processing = True
            break
        if status_payload.get("status") in {"completed", "failed"}:
            break
        time.sleep(0.4)

    if observed_processing:
        processing_results = api_client.get(f"{API_BASE}/screener/results/{batch_id}", timeout=30)
        processing_analytics = api_client.get(f"{API_BASE}/screener/analytics/{batch_id}", timeout=30)
        processing_export = api_client.get(f"{API_BASE}/screener/export/{batch_id}", timeout=30)

        assert processing_results.status_code == 409
        assert processing_analytics.status_code == 409
        assert processing_export.status_code == 409

    final_status = None
    for _ in range(80):
        status_resp = api_client.get(f"{API_BASE}/screener/status/{batch_id}", timeout=30)
        assert status_resp.status_code == 200
        final_status = status_resp.json()
        if final_status.get("status") in {"completed", "failed"}:
            break
        time.sleep(1)

    assert final_status is not None
    assert final_status.get("status") == "completed"

    completed_results = api_client.get(f"{API_BASE}/screener/results/{batch_id}", timeout=60)
    completed_analytics = api_client.get(f"{API_BASE}/screener/analytics/{batch_id}", timeout=60)
    completed_export = api_client.get(f"{API_BASE}/screener/export/{batch_id}", timeout=60)

    assert completed_results.status_code == 200
    assert completed_analytics.status_code == 200
    assert completed_export.status_code == 200


def test_analyze_rejects_missing_jd(api_client):
    zip_bytes = _build_resume_zip()
    response = api_client.post(
        f"{API_BASE}/screener/analyze",
        files={"resumes_zip": ("resumes.zip", zip_bytes, "application/zip")},
        timeout=30,
    )
    assert response.status_code == 400
    assert "job description" in response.json().get("detail", "").lower()


def test_analyze_rejects_non_zip_upload(api_client):
    response = api_client.post(
        f"{API_BASE}/screener/analyze",
        data={"jd_text": "Need python engineer"},
        files={"resumes_zip": ("not_zip.txt", b"resume text", "text/plain")},
        timeout=30,
    )
    assert response.status_code == 400
    assert "zip" in response.json().get("detail", "").lower()


def test_analyze_rejects_malformed_zip_content(api_client):
    response = api_client.post(
        f"{API_BASE}/screener/analyze",
        data={"jd_text": "Need python engineer"},
        files={"resumes_zip": ("fake.zip", b"not a real zip payload", "application/zip")},
        timeout=30,
    )
    assert response.status_code == 400
    assert "invalid zip" in response.json().get("detail", "").lower()


def test_recruiter_signup_login_and_me(api_client):
    unique_id = str(uuid.uuid4())[:8]
    email = f"recruiter_{unique_id}@example.com"
    password = "SecurePass@123"

    signup_response = api_client.post(
        f"{API_BASE}/auth/recruiters/signup",
        json={
            "name": "Test Recruiter",
            "email": email,
            "company": "Acme Hiring",
            "role": "Talent Acquisition",
            "password": password,
            "confirm_password": password,
        },
        timeout=60,
    )
    assert signup_response.status_code == 200
    signup_data = signup_response.json()
    assert signup_data["token_type"] == "bearer"
    assert signup_data["recruiter"]["email"] == email

    login_response = api_client.post(
        f"{API_BASE}/auth/recruiters/login",
        json={
            "email": email,
            "password": password,
            "remember_me": True,
        },
        timeout=60,
    )
    assert login_response.status_code == 200
    login_data = login_response.json()
    token = login_data["access_token"]

    me_response = api_client.get(
        f"{API_BASE}/auth/recruiters/me",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    assert me_response.status_code == 200
    me_data = me_response.json()
    assert me_data["email"] == email
