import asyncio
import base64
import io
import json
import logging
import os
import re
import uuid
import zipfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from groq import Groq

import pandas as pd
import pdfplumber
import pytesseract
import requests
from bs4 import BeautifulSoup
from docx import Document
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from github import Auth, Github
from jose import JWTError, jwt
from motor.motor_asyncio import AsyncIOMotorClient
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field
from PyPDF2 import PdfReader
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from starlette.middleware.cors import CORSMiddleware

from services.resume_classifier import compute_category_alignment, load_resume_classifier, predict_resume_category


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

app = FastAPI(title="AI Resume Screener API")
api_router = APIRouter(prefix="/api")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

JWT_SECRET_KEY = os.environ["JWT_SECRET_KEY"]
JWT_ALGORITHM = os.environ["JWT_ALGORITHM"]
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ["JWT_ACCESS_TOKEN_EXPIRE_MINUTES"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
auth_bearer = HTTPBearer(auto_error=False)

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
PROXYCURL_API_KEY = os.environ.get("PROXYCURL_API_KEY", "")
LINKEDIN_LI_AT = os.environ.get("LINKEDIN_LI_AT", "")
LINKEDIN_JSESSIONID = os.environ.get("LINKEDIN_JSESSIONID", "")
GITHUB_CLIENT = (
    Github(auth=Auth.Token(GITHUB_TOKEN), per_page=20)
    if GITHUB_TOKEN
    else Github(per_page=20)
)
GITHUB_CACHE_TTL_HOURS = 24
GITHUB_REPO_ANALYZE_LIMIT = int(os.environ.get("GITHUB_REPO_ANALYZE_LIMIT", "2"))
GITHUB_PORTFOLIO_CACHE: Dict[str, Dict[str, Any]] = {}

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
_groq_client: Optional[Groq] = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

LLM_STATS: Dict[str, int] = {"github_fallback_calls": 0, "tech_stack_calls": 0, "total_calls": 0}


STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "he",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "that",
    "the",
    "to",
    "was",
    "were",
    "will",
    "with",
    "you",
    "your",
}

SKILL_DICTIONARY = [
    "python",
    "java",
    "javascript",
    "typescript",
    "react",
    "node",
    "fastapi",
    "django",
    "flask",
    "sql",
    "postgresql",
    "mongodb",
    "redis",
    "docker",
    "kubernetes",
    "aws",
    "azure",
    "gcp",
    "machine learning",
    "deep learning",
    "nlp",
    "tensorflow",
    "pytorch",
    "scikit-learn",
    "data analysis",
    "pandas",
    "numpy",
    "git",
    "github",
    "ci/cd",
    "rest",
    "rest api",
    "microservices",
    "html",
    "css",
    "tailwind",
    "figma",
    "power bi",
    "tableau",
]

DEGREE_LEVELS = {
    "high school": 1,
    "diploma": 2,
    "associate": 3,
    "bachelor": 4,
    "b.tech": 4,
    "bsc": 4,
    "ba": 4,
    "master": 5,
    "m.tech": 5,
    "mba": 5,
    "msc": 5,
    "phd": 6,
    "doctorate": 6,
}


class HybridExtractor:
    """95% rule-based extraction with Groq Llama 3.1 70B fallback for edge cases."""

    def groq_github_fallback(self, resume_text: str) -> List[str]:
        """Ask LLM to find a GitHub username when regex+annotation+OCR all failed."""
        if not _groq_client:
            return []
        try:
            response = _groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Extract the GitHub username or profile URL from the following resume text.\n"
                            "Return a JSON object with a single key 'github' whose value is the full "
                            "GitHub profile URL (e.g. https://github.com/username) or null if not found.\n"
                            "Return ONLY valid JSON, no explanation.\n\n"
                            f"Resume (first 4000 chars):\n{resume_text[:4000]}"
                        ),
                    }
                ],
                temperature=0.1,
                max_tokens=120,
            )
            raw = response.choices[0].message.content or ""
            raw = raw.strip()
            json_match = re.search(r"\{.*?\}", raw, re.DOTALL)
            if not json_match:
                return []
            data = json.loads(json_match.group())
            github_val = data.get("github") or ""
            if not github_val or not isinstance(github_val, str):
                return []
            if "github.com" not in github_val.lower():
                github_val = f"https://github.com/{github_val.lstrip('@').strip()}"
            LLM_STATS["github_fallback_calls"] += 1
            LLM_STATS["total_calls"] += 1
            logger.info("Groq GitHub fallback found: %s", github_val)
            return [github_val]
        except Exception as exc:
            logger.warning("Groq GitHub fallback failed: %s", exc)
            return []

    def groq_tech_stack_extraction(self, jd_text: str) -> List[str]:
        """Use LLM to extract tech stack from fuzzy/non-standard JD text."""
        if not _groq_client:
            return []
        try:
            response = _groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Extract the exact technology/skill names required in this job description.\n"
                            "Return a JSON object: {\"tech_stack\": [\"Python\", \"React\", ...]}\n"
                            "Include programming languages, frameworks, databases, cloud tools, and dev tools.\n"
                            "Return ONLY valid JSON, no explanation.\n\n"
                            f"Job Description:\n{jd_text[:5000]}"
                        ),
                    }
                ],
                temperature=0.1,
                max_tokens=300,
            )
            raw = response.choices[0].message.content or ""
            raw = raw.strip()
            json_match = re.search(r"\{.*?\}", raw, re.DOTALL)
            if not json_match:
                return []
            data = json.loads(json_match.group())
            stack = data.get("tech_stack") or []
            if not isinstance(stack, list):
                return []
            skills = [str(s).lower().strip() for s in stack if isinstance(s, str) and s.strip()]
            LLM_STATS["tech_stack_calls"] += 1
            LLM_STATS["total_calls"] += 1
            logger.info("Groq tech stack extracted %d skills", len(skills))
            return skills
        except Exception as exc:
            logger.warning("Groq tech stack extraction failed: %s", exc)
            return []


hybrid_extractor = HybridExtractor()


class GitHubActivity(BaseModel):
    valid: bool = False
    username: Optional[str] = None
    repo_count: int = 0
    last_active: Optional[str] = None
    top_languages: List[str] = Field(default_factory=list)
    bonus_points: float = 0.0
    notes: str = ""


class LinkScanResult(BaseModel):
    url: str
    link_type: str
    reachable: bool = False
    valid_format: bool = True
    status_code: Optional[int] = None
    notes: str = ""


class ProjectVerification(BaseModel):
    repo_name: str
    repo_url: str
    project_type: str
    complexity_score: float
    complexity_label: str
    tech_stack: List[str] = Field(default_factory=list)
    jd_matched_tech: List[str] = Field(default_factory=list)
    jd_stack_coverage_pct: float = 0.0
    stars: int = 0
    forks: int = 0
    estimated_file_count: int = 0
    contributors: int = 0
    tests_present: bool = False
    deployment_ready: bool = False
    last_commit_at: Optional[str] = None
    activity_status: str = "Stale"
    description: str = ""
    readme_preview: str = ""


class GitHubPortfolioAnalysis(BaseModel):
    verified: bool = False
    profile_url: Optional[str] = None
    username: Optional[str] = None
    total_public_repos: int = 0
    repos_analyzed: int = 0
    jd_relevant_projects: int = 0
    stack_coverage_pct: float = 0.0
    best_project_complexity: float = 0.0
    activity_status: str = "Unknown"
    verification_score: float = 0.0
    top_projects: List[ProjectVerification] = Field(default_factory=list)
    notes: str = ""


class LinkedInPortfolioAnalysis(BaseModel):
    verified: bool = False
    profile_url: Optional[str] = None
    headline: Optional[str] = None
    current_title: Optional[str] = None
    total_experience_years: int = 0
    projects_found: int = 0
    jd_keywords_found: List[str] = Field(default_factory=list)
    connections_count: int = 0
    premium_detected: bool = False
    verification_score: float = 0.0
    notes: str = ""
    achievements: List[str] = Field(default_factory=list)
    certifications: List[str] = Field(default_factory=list)
    project_titles: List[str] = Field(default_factory=list)


class SmartPortfolioSummary(BaseModel):
    github_weight: float = 70.0
    linkedin_weight: float = 30.0
    github_score: float = 0.0
    linkedin_score: float = 0.0
    verification_bonus: float = 0.0
    stack_experience_verified: bool = False
    hr_insight: str = ""


class VerifiedLinks(BaseModel):
    github_url: Optional[str] = None
    linkedin_url: Optional[str] = None
    portfolio_url: Optional[str] = None
    github_urls: List[str] = Field(default_factory=list)
    linkedin_urls: List[str] = Field(default_factory=list)
    portfolio_urls: List[str] = Field(default_factory=list)
    scanned_links: List[LinkScanResult] = Field(default_factory=list)
    github: GitHubActivity = Field(default_factory=GitHubActivity)
    github_analysis: GitHubPortfolioAnalysis = Field(default_factory=GitHubPortfolioAnalysis)
    linkedin_analysis: LinkedInPortfolioAnalysis = Field(
        default_factory=LinkedInPortfolioAnalysis
    )
    smart_portfolio: SmartPortfolioSummary = Field(default_factory=SmartPortfolioSummary)
    linkedin_valid: bool = False
    linkedin_reachable: bool = False
    portfolio_reachable: bool = False
    activity_bonus: float = 0.0


class ATSScore(BaseModel):
    score: int = 100
    label: str = "Green"
    issues: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)


class VerificationCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class VerificationSummary(BaseModel):
    status: str = "verified_partially"
    checks_passed: int = 0
    checks_total: int = 5
    checks: List[VerificationCheck] = Field(default_factory=list)


class EmailTemplates(BaseModel):
    template_type: str = "advance"
    subject: str = ""
    body: str = ""


class GitHubAnalysisSummary(BaseModel):
    username: str = ""
    top_project: str = ""
    stack_coverage_pct: float = 0.0
    activity_status: str = "Unknown"


class LinkedInSummary(BaseModel):
    verified: bool = False
    headline: str = ""
    notes: str = ""


class ScannedLinksSummary(BaseModel):
    total_links: int = 0
    reachable_links: int = 0
    github_found: bool = False
    linkedin_found: bool = False
    portfolio_reachable: bool = False


class NotableAchievement(BaseModel):
    title: str
    achievement_type: str  # "github" | "hackathon" | "certification" | "linkedin" | "portfolio"
    score: float = 0.0
    stars: int = 0
    forks: int = 0
    deployed_url: Optional[str] = None
    url: Optional[str] = None
    description: str = ""
    medal: str = "🥉"


class NotableAchievements(BaseModel):
    top_achievements: List[NotableAchievement] = Field(default_factory=list)
    total_found: int = 0


class CandidateResult(BaseModel):
    candidate_id: str
    candidate_name: str
    candidate_email: Optional[str] = None
    source_file: str
    fit_score: float
    tier: str
    similarity_score: float
    skills_match_score: float
    experience_match_score: float
    education_match_score: float
    matched_skills: List[str] = Field(default_factory=list)
    missing_skills: List[str] = Field(default_factory=list)
    suggested_improvements: List[str] = Field(default_factory=list)
    extracted_years_experience: int = 0
    verified_links: VerifiedLinks
    github_extraction_method: str = "rule-based"
    ats_score: ATSScore = Field(default_factory=ATSScore)
    verification_summary: VerificationSummary = Field(
        default_factory=VerificationSummary
    )
    evidence_summary: str = ""
    shortlist_recommendation: Literal["Advance", "Review", "Hold"] = "Review"
    github_analysis_summary: GitHubAnalysisSummary = Field(default_factory=GitHubAnalysisSummary)
    linkedin_summary: LinkedInSummary = Field(default_factory=LinkedInSummary)
    scanned_links_summary: ScannedLinksSummary = Field(default_factory=ScannedLinksSummary)
    email_template: EmailTemplates = Field(default_factory=EmailTemplates)
    notable_achievements: NotableAchievements = Field(default_factory=NotableAchievements)
    predicted_category: Optional[str] = None
    category_confidence: float = 0.0
    category_alignment_score: int = 0
    category_alignment_label: str = ""


class SkillCoverage(BaseModel):
    skill: str
    matched_count: int


class CandidateScore(BaseModel):
    candidate_name: str
    fit_score: float
    tier: str


class AnalysisAnalytics(BaseModel):
    resumes_uploaded: int
    average_fit_score: float
    candidates_above_80: int
    score_distribution: Dict[str, int]
    skill_coverage: List[SkillCoverage] = Field(default_factory=list)
    candidate_scores: List[CandidateScore] = Field(default_factory=list)
    category_distribution: Dict[str, int] = Field(default_factory=dict)


class AnalysisResponse(BaseModel):
    batch_id: str
    generated_at: str
    jd_keywords: List[str] = Field(default_factory=list)
    required_skills: List[str] = Field(default_factory=list)
    nice_to_have_skills: List[str] = Field(default_factory=list)
    processing_logs: List[str] = Field(default_factory=list)
    results: List[CandidateResult] = Field(default_factory=list)
    analytics: AnalysisAnalytics
    llm_tech_stack_enhanced: bool = False


class ScreenerStatusResponse(BaseModel):
    batch_id: str
    status: str
    progress: int = 0
    processing_logs: List[str] = Field(default_factory=list)
    error_message: Optional[str] = None
    completed: bool = False


class RecruiterSignupRequest(BaseModel):
    name: str
    email: EmailStr
    company: str
    role: str
    password: str
    confirm_password: str


class RecruiterLoginRequest(BaseModel):
    email: EmailStr
    password: str
    remember_me: bool = True


class RecruiterProfile(BaseModel):
    recruiter_id: str
    name: str
    email: EmailStr
    company: str
    role: str
    created_at: str


class RecruiterAuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    recruiter: RecruiterProfile


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def tokenize_text(value: str) -> List[str]:
    tokens = re.findall(r"[a-zA-Z][a-zA-Z+#.\-]{1,}", value.lower())
    return [token for token in tokens if token not in STOP_WORDS and len(token) > 2]


def extract_top_keywords(value: str, limit: int = 25) -> List[str]:
    counts = Counter(tokenize_text(value))
    return [token for token, _ in counts.most_common(limit)]


def extract_required_skills(jd_text: str) -> List[str]:
    jd_lower = jd_text.lower()
    return [skill for skill in SKILL_DICTIONARY if skill in jd_lower]


def extract_nice_to_have_skills(jd_text: str) -> List[str]:
    jd_lower = jd_text.lower()
    nice_section = ""
    marker_match = re.search(r"nice[-\s]?to[-\s]?have\s*:?(.+)", jd_lower)
    if marker_match:
        nice_section = marker_match.group(1)
    return [skill for skill in SKILL_DICTIONARY if skill in nice_section]


def extract_experience_years(value: str) -> int:
    year_matches = re.findall(r"(\d{1,2})\s*\+?\s*(?:years|yrs|year)", value.lower())
    numbers = [int(num) for num in year_matches]
    return max(numbers) if numbers else 0


def extract_education_level(value: str) -> int:
    text = value.lower()
    detected = [level for degree, level in DEGREE_LEVELS.items() if degree in text]
    return max(detected) if detected else 0


def calculate_experience_score(candidate_years: int, jd_years: int) -> float:
    if jd_years <= 0:
        return 70.0 if candidate_years > 0 else 60.0
    if candidate_years <= 0:
        return 30.0
    return round(min(100.0, (candidate_years / jd_years) * 100), 2)


def calculate_education_score(candidate_level: int, jd_level: int) -> float:
    if jd_level <= 0:
        return 70.0 if candidate_level > 0 else 60.0
    if candidate_level >= jd_level:
        return 100.0
    if candidate_level == jd_level - 1:
        return 70.0
    return 40.0


def classify_tier(score: float) -> str:
    if score >= 80:
        return "Top Tier"
    if score >= 60:
        return "Middle Tier"
    return "Low Tier"


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(subject_email: str, remember_me: bool = True) -> str:
    lifetime_minutes = JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    if remember_me:
        lifetime_minutes = JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 24 * 7
    expire = datetime.now(timezone.utc) + timedelta(minutes=lifetime_minutes)
    # Numeric exp (Unix seconds) — required for reliable decode across python-jose / clients
    payload = {"sub": subject_email.lower(), "exp": int(expire.timestamp())}
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def serialize_recruiter_profile(doc: Dict[str, Any]) -> RecruiterProfile:
    return RecruiterProfile(
        recruiter_id=doc["recruiter_id"],
        name=doc["name"],
        email=doc["email"],
        company=doc["company"],
        role=doc["role"],
        created_at=doc["created_at"],
    )


async def get_current_recruiter(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(auth_bearer),
) -> RecruiterProfile:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Authentication required")

    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        email = payload.get("sub")
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc

    if not email:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    recruiter = await db.recruiters.find_one({"email": email.lower()}, {"_id": 0})
    if not recruiter:
        raise HTTPException(status_code=401, detail="Recruiter not found")

    return serialize_recruiter_profile(recruiter)


def extract_urls(value: str) -> List[str]:
    return re.findall(r"https?://[^\s<>)\]]+", value)


def normalize_external_url(url: str) -> str:
    cleaned = url.strip().strip(".,;:)]}")
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        return cleaned
    return f"https://{cleaned}"


def infer_social_urls_from_text(value: str) -> List[str]:
    text = value or ""
    links: List[str] = []

    direct_github = re.findall(
        r"(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?",
        text,
        flags=re.IGNORECASE,
    )
    for item in direct_github:
        links.append(normalize_external_url(item))

    direct_linkedin = re.findall(
        r"(?:https?://)?(?:www\.)?linkedin\.com/(?:in|pub|company)/[A-Za-z0-9_.-]+",
        text,
        flags=re.IGNORECASE,
    )
    for item in direct_linkedin:
        links.append(normalize_external_url(item))

    explicit_github_handles = re.findall(
        r"github\s*(?:link|profile|id)?\s*[:\-]\s*@?([A-Za-z0-9][A-Za-z0-9-]{2,})\b",
        text,
        flags=re.IGNORECASE,
    )
    for handle in explicit_github_handles:
        if handle.lower() not in {"github", "link", "profile", "portfolio", "http", "https"}:
            links.append(f"https://github.com/{handle}")

    explicit_linkedin_handles = re.findall(
        r"linkedin\s*(?:link|profile|id)?\s*[:\-]\s*@?([A-Za-z0-9][A-Za-z0-9-]{2,})\b",
        text,
        flags=re.IGNORECASE,
    )
    for handle in explicit_linkedin_handles:
        if handle.lower() not in {"linkedin", "link", "profile", "portfolio", "github", "http", "https"}:
            links.append(f"https://www.linkedin.com/in/{handle}")

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line_lower = line.lower()

        if "github" in line_lower and "github.com" not in line_lower:
            github_handle_match = re.search(
                r"(?:github|git\s*hub)(?:\s*(?:link|profile|id))?\s*[:|\-]?\s*@?([A-Za-z0-9][A-Za-z0-9-]{2,})\b",
                line,
                flags=re.IGNORECASE,
            )
            if github_handle_match:
                handle = github_handle_match.group(1)
                if handle.lower() not in {
                    "link",
                    "profile",
                    "github",
                    "portfolio",
                    "http",
                    "https",
                    "www",
                }:
                    links.append(f"https://github.com/{handle}")

        if "linkedin" in line_lower and "linkedin.com" not in line_lower:
            linkedin_handle_match = re.search(
                r"(?:linkedin|linked\s*in)(?:\s*(?:link|profile|id))?\s*[:|\-]?\s*@?([A-Za-z0-9][A-Za-z0-9-]{2,})\b",
                line,
                flags=re.IGNORECASE,
            )
            if linkedin_handle_match:
                handle = linkedin_handle_match.group(1)
                if handle.lower() not in {
                    "link",
                    "profile",
                    "linkedin",
                    "portfolio",
                    "github",
                    "http",
                    "https",
                    "www",
                }:
                    links.append(f"https://www.linkedin.com/in/{handle}")

    domain_candidates = re.findall(
        r"\b(?:[a-zA-Z0-9-]+\.)+(?:com|dev|io|ai|me|org|in|co)\b",
        text,
        flags=re.IGNORECASE,
    )
    text_lower = text.lower()
    existing_lower_links = [item.lower() for item in links]
    for domain in domain_candidates:
        lower_domain = domain.lower()
        if "github.com" in lower_domain or "linkedin.com" in lower_domain:
            continue
        if re.search(rf"https?://(?:www\.)?{re.escape(lower_domain)}/", text_lower):
            continue
        if any(lower_domain in current for current in existing_lower_links):
            continue
        links.append(normalize_external_url(domain))

    return unique_links(links)


def collect_urls_from_nested_object(payload: Any, output: List[str]) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                collect_urls_from_nested_object(value, output)
            elif isinstance(value, str) and value.lower().startswith(("http://", "https://")):
                output.append(value)
            elif key.lower() in {"uri", "url"} and isinstance(value, str):
                if value.lower().startswith(("http://", "https://")):
                    output.append(value)
    elif isinstance(payload, list):
        for item in payload:
            collect_urls_from_nested_object(item, output)


def extract_pdf_annotation_links(file_bytes: bytes) -> List[str]:
    links: List[str] = []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                annots = page.annots or []
                for annot in annots:
                    collect_urls_from_nested_object(annot, links)

                hyperlinks = getattr(page, "hyperlinks", None) or []
                for hyperlink in hyperlinks:
                    collect_urls_from_nested_object(hyperlink, links)
    except Exception:
        return []

    normalized = [normalize_external_url(url) for url in links if isinstance(url, str)]
    return unique_links(normalized)


def extract_pdf_ocr_text(file_bytes: bytes, max_pages: int = 2) -> str:
    try:
        text_chunks: List[str] = []
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages[:max_pages]:
                try:
                    image = page.to_image(resolution=160).original
                    ocr_text = pytesseract.image_to_string(image)
                    if ocr_text:
                        text_chunks.append(ocr_text)
                except Exception:
                    continue
        return normalize_text(" ".join(text_chunks))
    except Exception:
        return ""


def unique_links(links: List[str]) -> List[str]:
    return list(dict.fromkeys(links))


def classify_link_type(url: str) -> str:
    lowered = url.lower()
    if "github.com" in lowered:
        return "github"
    if "linkedin.com" in lowered:
        return "linkedin"
    if re.search(r"(portfolio|behance|dribbble|medium|dev\.to|substack)", lowered):
        return "portfolio"
    return "other"


def extract_github_username(url: str) -> Optional[str]:
    match = re.search(r"github\.com/([A-Za-z0-9-]+)", url)
    if not match:
        return None
    username = match.group(1)
    if username.lower() in {"features", "topics", "marketplace", "orgs"}:
        return None
    return username


_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


def safe_url_scan(url: str) -> Dict[str, Any]:
    try:
        response = requests.get(
            url, timeout=4, allow_redirects=True, headers=_BROWSER_HEADERS
        )
        reachable = response.status_code < 400 or response.status_code in {401, 403, 429, 999}
        return {
            "reachable": reachable,
            "status_code": response.status_code,
            "final_url": str(response.url),
            "error": "",
        }
    except Exception as exc:
        return {
            "reachable": False,
            "status_code": None,
            "final_url": url,
            "error": str(exc),
        }


def verify_linkedin(url: str) -> Dict[str, Any]:
    valid = bool(re.search(r"linkedin\.com/(in|pub|company)/", url))
    result = safe_url_scan(url) if valid else {"reachable": False, "status_code": None}
    return {
        "valid": valid,
        "reachable": bool(result["reachable"]),
        "status_code": result["status_code"],
        "notes": "Valid LinkedIn format" if valid else "Invalid LinkedIn URL format",
    }


def verify_portfolio(url: str) -> Dict[str, Any]:
    result = safe_url_scan(url)
    return {
        "reachable": bool(result["reachable"]),
        "status_code": result["status_code"],
        "notes": "Reachable" if result["reachable"] else "Unreachable",
    }


def verify_generic_link(url: str) -> Dict[str, Any]:
    result = safe_url_scan(url)
    return {
        "reachable": bool(result["reachable"]),
        "status_code": result["status_code"],
        "notes": "Reachable" if result["reachable"] else "Unreachable",
    }


def map_skills_to_languages(skills: List[str]) -> List[str]:
    mapping = {
        "python": "python",
        "java": "java",
        "javascript": "javascript",
        "typescript": "typescript",
        "react": "javascript",
        "node": "javascript",
        "pytorch": "python",
        "tensorflow": "python",
        "scikit-learn": "python",
    }
    output = set()
    for skill in skills:
        if skill in mapping:
            output.add(mapping[skill])
    return list(output)


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def get_github_cache(cache_key: str) -> Optional[Dict[str, Any]]:
    cached = GITHUB_PORTFOLIO_CACHE.get(cache_key)
    if not cached:
        return None
    cached_at = parse_iso_datetime(cached.get("cached_at"))
    if not cached_at:
        return None
    if (datetime.now(timezone.utc) - cached_at).total_seconds() > GITHUB_CACHE_TTL_HOURS * 3600:
        GITHUB_PORTFOLIO_CACHE.pop(cache_key, None)
        return None
    return cached.get("data")


def set_github_cache(cache_key: str, data: Dict[str, Any]) -> None:
    GITHUB_PORTFOLIO_CACHE[cache_key] = {
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }


def decode_readme_content(raw_readme: Any) -> str:
    if not raw_readme:
        return ""
    content = getattr(raw_readme, "decoded_content", b"")
    if not content:
        return ""
    try:
        return content.decode("utf-8", errors="ignore").lower()
    except Exception:
        try:
            return base64.b64decode(content).decode("utf-8", errors="ignore").lower()
        except Exception:
            return ""


def infer_repo_tech_stack(
    repo_name: str,
    repo_description: str,
    repo_topics: List[str],
    languages: Dict[str, int],
    readme_text: str,
    root_file_names: List[str],
) -> List[str]:
    bag = " ".join(
        [repo_name.lower(), repo_description.lower(), readme_text, " ".join(repo_topics), " ".join(root_file_names)]
    )
    lang_names = [name.lower() for name in languages.keys()]
    inferred: List[str] = []

    tech_patterns = {
        "python": ["python"],
        "javascript": ["javascript", "js"],
        "typescript": ["typescript", "tsconfig"],
        "react": ["react", "next.js", "nextjs", "vite"],
        "node": ["node", "express", "nestjs"],
        "fastapi": ["fastapi"],
        "flask": ["flask"],
        "django": ["django"],
        "docker": ["docker", "dockerfile", "docker-compose"],
        "postgresql": ["postgres", "postgresql", "psql"],
        "aws": ["aws", "s3", "lambda", "ec2", "cloudformation"],
        "redis": ["redis"],
        "ci/cd": ["github actions", "ci/cd", "workflow", "jenkins"],
        "machine learning": ["machine learning", "ml", "model"],
        "scikit-learn": ["scikit-learn", "sklearn"],
        "tensorflow": ["tensorflow", "keras"],
        "pytorch": ["pytorch", "torch"],
        "rest api": ["rest", "openapi", "swagger", "api"],
    }

    for tech, markers in tech_patterns.items():
        if any(marker in bag for marker in markers):
            inferred.append(tech)

    for lang in lang_names:
        if lang in {"python", "javascript", "typescript", "java", "go"}:
            inferred.append(lang)

    return sorted(list(set(inferred)))


def calculate_jd_stack_coverage(
    languages: Dict[str, int], repo_tech_stack: List[str], jd_required_skills: List[str]
) -> Dict[str, Any]:
    if not jd_required_skills:
        return {"coverage_pct": 0.0, "matched_skills": []}

    total_bytes = sum(languages.values()) or 1
    matched_bytes = 0.0
    matched_skills: List[str] = []
    lower_tech_stack = {skill.lower() for skill in repo_tech_stack}

    language_skill_map = {
        "python": ["python"],
        "java": ["java"],
        "javascript": ["javascript", "typescript"],
        "typescript": ["typescript", "javascript"],
        "react": ["javascript", "typescript"],
        "fastapi": ["python"],
        "postgresql": ["sql", "plpgsql"],
        "aws": ["yaml", "python", "javascript"],
        "docker": ["dockerfile"],
        "redis": ["python", "javascript", "go"],
        "ci/cd": ["yaml"],
    }

    for skill in jd_required_skills:
        normalized = skill.lower()
        by_presence = normalized in lower_tech_stack
        mapped_languages = language_skill_map.get(normalized, [normalized])
        language_bytes = sum(
            byte_count
            for language_name, byte_count in languages.items()
            if language_name.lower() in mapped_languages
        )

        if by_presence or language_bytes > 0:
            matched_skills.append(skill)
            if language_bytes > 0:
                matched_bytes += language_bytes
            else:
                matched_bytes += total_bytes * 0.20 / max(1, len(jd_required_skills))

    coverage_pct = round(min(100.0, (matched_bytes / total_bytes) * 100), 2)
    if coverage_pct == 0 and matched_skills:
        coverage_pct = round((len(matched_skills) / len(jd_required_skills)) * 100, 2)

    return {"coverage_pct": coverage_pct, "matched_skills": matched_skills}


def classify_project_type(
    repo_tech_stack: List[str], root_file_names: List[str], estimated_file_count: int
) -> str:
    lower_stack = {item.lower() for item in repo_tech_stack}
    lower_files = {item.lower() for item in root_file_names}

    has_frontend = bool(lower_stack.intersection({"react", "javascript", "typescript"}))
    has_backend = bool(lower_stack.intersection({"python", "node", "fastapi", "django", "flask", "java"}))
    has_ml = bool(lower_stack.intersection({"machine learning", "tensorflow", "pytorch", "scikit-learn"}))
    has_mobile = bool(
        lower_stack.intersection({"react native", "flutter"})
        or {"androidmanifest.xml", "pubspec.yaml", "ios"}.intersection(lower_files)
    )
    has_api = bool(lower_stack.intersection({"rest api", "fastapi", "flask", "django", "node"}))

    if has_frontend and has_backend:
        return "Web Application"
    if has_ml:
        return "ML/AI Project"
    if has_mobile:
        return "Mobile App"
    if has_api:
        return "API/Backend Service"
    if estimated_file_count < 10 and not has_frontend and not has_backend:
        return "Basic/Script Project"
    return "General Software Project"


def classify_complexity_label(score: float) -> str:
    if score <= 3:
        return "BEGINNER"
    if score <= 6:
        return "INTERMEDIATE"
    return "ADVANCED"


def calculate_complexity_score(
    stars: int,
    forks: int,
    estimated_file_count: int,
    contributors_count: int,
    tests_present: bool,
    test_file_count: int,
    deployment_ready: bool,
    env_configs_count: int,
    recent_commits: bool,
    weekly_activity: bool,
) -> float:
    base_score = 1.0
    score = base_score
    score += min(2.0, stars / 10)
    score += min(1.0, forks / 20)
    score += min(1.5, estimated_file_count / 100)
    score += min(1.0, contributors_count * 0.3)
    if tests_present:
        score += 1.0
    if test_file_count >= 2:
        score += 0.5
    if deployment_ready:
        score += 1.0
    if env_configs_count >= 2:
        score += 0.5
    if recent_commits:
        score += 1.0
    if weekly_activity:
        score += 0.5
    return round(min(10.0, score), 2)


def get_activity_status(last_commit: Optional[str]) -> str:
    commit_dt = parse_iso_datetime(last_commit)
    if not commit_dt:
        return "Stale"
    days_old = (datetime.now(timezone.utc) - commit_dt).days
    if days_old <= 45:
        return "Active"
    if days_old <= 180:
        return "Recent"
    return "Stale"


def extract_linkedin_connections(text: str) -> int:
    match = re.search(r"(\d{2,4}[\+,]?)\s+connections", text)
    if not match:
        return 0
    raw = match.group(1).replace(",", "").replace("+", "")
    if raw.isdigit():
        return int(raw)
    return 0


def _proxycurl_fetch_linkedin(url: str) -> Optional[Dict[str, Any]]:
    """
    Call Proxycurl Person Profile API.
    Returns the raw profile dict on success, None if unavailable/unconfigured.
    Free tier: 10 credits at https://nubela.co/proxycurl
    """
    if not PROXYCURL_API_KEY:
        return None
    try:
        resp = requests.get(
            "https://nubela.co/proxycurl/api/v2/linkedin",
            params={
                "linkedin_profile_url": url,
                "use_cache": "if-present",   # free — reuse cached data if already fetched
                "skills": "include",
                "extra": "include",
            },
            headers={"Authorization": f"Bearer {PROXYCURL_API_KEY}"},
            timeout=20,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("full_name") or data.get("headline"):
                logger.info("Proxycurl LinkedIn data fetched for %s", url)
                return data
        elif resp.status_code == 404:
            logger.info("Proxycurl: LinkedIn profile not found: %s", url)
        elif resp.status_code == 401:
            logger.warning("Proxycurl: invalid API key")
        elif resp.status_code == 429:
            logger.warning("Proxycurl: rate limit / credits exhausted")
        else:
            logger.warning("Proxycurl returned %s for %s", resp.status_code, url)
    except Exception as exc:
        logger.warning("Proxycurl request failed: %s", exc)
    return None


def _proxycurl_to_linkedin_analysis(
    data: Dict[str, Any],
    url: str,
    jd_required_skills: List[str],
    jd_nice_to_have: List[str],
) -> LinkedInPortfolioAnalysis:
    """Convert Proxycurl API response to LinkedInPortfolioAnalysis."""
    jd_terms = sorted(set([*jd_required_skills, *jd_nice_to_have]))

    # Skills from Proxycurl
    profile_skills = [s.get("name", "").lower() for s in (data.get("skills") or [])]
    jd_keywords_found = [t for t in jd_terms if t.lower() in profile_skills
                         or t.lower() in (data.get("summary") or "").lower()
                         or t.lower() in (data.get("headline") or "").lower()]

    # Experience years from roles
    experiences = data.get("experiences") or []
    total_exp_years = 0
    for exp in experiences:
        starts_at = exp.get("starts_at") or {}
        ends_at = exp.get("ends_at") or {}
        start_year = starts_at.get("year")
        end_year = ends_at.get("year") or datetime.now().year
        if start_year:
            total_exp_years += max(0, end_year - start_year)

    # Achievements from honors
    achievements = [
        h.get("title", "") for h in (data.get("accomplishment_honors_awards") or [])
        if h.get("title")
    ][:8]

    # Certifications
    certifications = [
        c.get("name", "") for c in (data.get("certifications") or [])
        if c.get("name")
    ][:6]

    # Project titles
    project_titles = [
        p.get("title", "") for p in (data.get("accomplishment_projects") or [])
        if p.get("title")
    ][:5]
    projects_found = len(project_titles) or len(data.get("accomplishment_projects") or [])

    # Connections
    connections = data.get("connections") or 0

    # Score
    score = 0.0
    if jd_keywords_found:       score += 10
    if projects_found > 0:      score += 8
    if achievements:            score += 7
    if certifications:          score += 5
    if connections >= 500:      score += 5
    if total_exp_years >= 2:    score += 3
    if data.get("summary"):     score += 2

    headline = data.get("headline") or data.get("occupation") or ""
    current_title = ""
    if experiences:
        current_title = experiences[0].get("title") or ""

    return LinkedInPortfolioAnalysis(
        verified=True,
        profile_url=url,
        headline=headline,
        current_title=current_title,
        total_experience_years=min(int(total_exp_years), 40),
        projects_found=projects_found,
        jd_keywords_found=jd_keywords_found,
        connections_count=connections,
        premium_detected=False,
        verification_score=round(score, 2),
        notes=f"Full LinkedIn profile via Proxycurl API · {len(profile_skills)} skills",
        achievements=achievements,
        certifications=certifications,
        project_titles=project_titles,
    )


async def _playwright_scrape_linkedin(url: str) -> Optional[Dict[str, Any]]:
    """
    Playwright-based LinkedIn scraper for college/dev use.
    Uses li_at session cookie to bypass authwall.
    Returns structured profile dict or None on failure.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("Playwright not installed — run: pip install playwright && playwright install chromium")
        return None

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-web-security",
                ],
            )

            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                timezone_id="America/New_York",
            )

            # Inject LinkedIn session cookies if available
            cookies = []
            if LINKEDIN_LI_AT:
                cookies.append({
                    "name": "li_at",
                    "value": LINKEDIN_LI_AT,
                    "domain": ".linkedin.com",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                })
            if LINKEDIN_JSESSIONID:
                jsessionid = LINKEDIN_JSESSIONID.strip('"')
                cookies.append({
                    "name": "JSESSIONID",
                    "value": f'"{jsessionid}"' if not jsessionid.startswith('"') else jsessionid,
                    "domain": ".linkedin.com",
                    "path": "/",
                    "httpOnly": False,
                    "secure": True,
                })
            if cookies:
                await context.add_cookies(cookies)

            page = await context.new_page()

            # Block images/fonts/media to speed up loading
            await page.route(
                "**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf,mp4,mp3}",
                lambda route: route.abort(),
            )

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=10000)
                await page.wait_for_timeout(1000)
            except Exception as nav_err:
                logger.warning("Playwright navigation failed for %s: %s", url, nav_err)
                await browser.close()
                return None

            current_url = page.url
            # Detect authwall / login redirect
            if any(k in current_url for k in ("authwall", "/login", "checkpoint", "uas/login")):
                logger.info(
                    "LinkedIn authwall hit for %s. "
                    "Set LINKEDIN_LI_AT cookie in backend/.env to bypass.",
                    url,
                )
                await browser.close()
                return {"_blocked": True, "profile_url": url}

            # ── Extract profile data ──────────────────────────────────────────
            data: Dict[str, Any] = {"profile_url": url, "_blocked": False}

            # Name / headline
            for sel, key in [
                ("h1", "full_name"),
                (".text-body-medium.break-words", "headline"),
                (".pv-text-details__left-panel .text-body-small", "location"),
            ]:
                try:
                    el = page.locator(sel).first
                    text = (await el.inner_text(timeout=2000)).strip()
                    if text:
                        data[key] = text
                except Exception:
                    pass

            # About / summary
            try:
                about = page.locator(".core-section-container.summary .core-section-container__content").first
                data["summary"] = (await about.inner_text(timeout=2000)).strip()
            except Exception:
                pass

            # Skills section
            skills: List[str] = []
            try:
                skill_items = page.locator(".pv-skill-category-entity__name span[aria-hidden='true']")
                count = await skill_items.count()
                for i in range(min(count, 20)):
                    sk = (await skill_items.nth(i).inner_text(timeout=1000)).strip()
                    if sk:
                        skills.append(sk)
            except Exception:
                pass
            data["skills"] = skills

            # Experience section
            experiences: List[Dict] = []
            try:
                exp_items = page.locator(".experience-section li, .pvs-list__item--line-separated")
                count = await exp_items.count()
                for i in range(min(count, 5)):
                    try:
                        item_text = (await exp_items.nth(i).inner_text(timeout=1000)).strip()
                        if item_text and len(item_text) > 3:
                            experiences.append({"title": item_text.split("\n")[0]})
                    except Exception:
                        pass
            except Exception:
                pass
            data["experiences"] = experiences

            # Certifications
            certifications: List[str] = []
            try:
                cert_section = page.locator(".certifications-section .pv-certification-entity__summary-info")
                count = await cert_section.count()
                for i in range(min(count, 6)):
                    try:
                        cert_name = await cert_section.nth(i).locator("h3").inner_text(timeout=1000)
                        if cert_name.strip():
                            certifications.append(cert_name.strip())
                    except Exception:
                        pass
            except Exception:
                pass
            data["certifications"] = certifications

            # Connections count from meta text
            try:
                conn_el = page.locator(".pv-top-card--list .pv-top-card--list-bullet li span").first
                conn_text = (await conn_el.inner_text(timeout=1500)).strip()
                m = re.search(r"(\d[\d,]+)\+?", conn_text)
                if m:
                    data["connections"] = int(m.group(1).replace(",", ""))
            except Exception:
                data["connections"] = 0

            # Get full page text for keyword matching
            try:
                data["_page_text"] = await page.inner_text("body")
            except Exception:
                data["_page_text"] = ""

            await browser.close()
            logger.info("Playwright LinkedIn scrape OK for %s — name=%s", url, data.get("full_name", "?"))
            return data

    except Exception as exc:
        logger.error("Playwright LinkedIn scraper error: %s", exc)
        return None


def _playwright_data_to_analysis(
    data: Dict[str, Any],
    url: str,
    jd_required_skills: List[str],
    jd_nice_to_have: List[str],
) -> LinkedInPortfolioAnalysis:
    """Convert Playwright-scraped LinkedIn data to LinkedInPortfolioAnalysis."""
    jd_terms = sorted(set([*jd_required_skills, *jd_nice_to_have]))
    page_text = (data.get("_page_text") or "").lower()

    # JD keyword matching against page text + skills
    profile_skills_lower = [s.lower() for s in (data.get("skills") or [])]
    jd_keywords_found = [
        t for t in jd_terms
        if t.lower() in page_text or t.lower() in profile_skills_lower
    ]

    # Experience years — rough estimate from page text
    total_experience_years = extract_experience_years(page_text)

    # Certifications
    certifications = [c for c in (data.get("certifications") or []) if c][:6]

    # Skills
    skills = data.get("skills") or []

    # Connections
    connections = int(data.get("connections") or 0)

    # Projects mentioned in page text
    projects_found = len(re.findall(r"\bproject(s)?\b", page_text))

    # Score
    score = 0.0
    if jd_keywords_found:        score += 10
    if projects_found > 0:       score += 8
    if certifications:           score += 5
    if connections >= 500:       score += 5
    if total_experience_years >= 2: score += 3
    if data.get("summary"):      score += 2
    if skills:                   score += 3

    cookie_note = "✅ Scraped with session cookie" if LINKEDIN_LI_AT else "⚠️ Scraped without cookie (partial)"

    return LinkedInPortfolioAnalysis(
        verified=True,
        profile_url=url,
        headline=data.get("headline") or data.get("full_name") or "",
        current_title=(data.get("experiences") or [{}])[0].get("title", ""),
        total_experience_years=total_experience_years,
        projects_found=projects_found,
        jd_keywords_found=jd_keywords_found,
        connections_count=connections,
        verification_score=round(score, 2),
        notes=f"LinkedIn scraped via Playwright · {cookie_note} · {len(skills)} skills found",
        certifications=certifications,
        project_titles=[e.get("title", "") for e in (data.get("experiences") or [])[:5]],
    )


def verify_linkedin_portfolio(
    url: Optional[str], jd_required_skills: List[str], jd_nice_to_have: List[str]
) -> LinkedInPortfolioAnalysis:
    """
    3-layer LinkedIn verification pipeline:
      Layer 1: Proxycurl API  — real structured data (requires PROXYCURL_API_KEY)
      Layer 2: Public HTML    — limited data from public page (often blocked by LinkedIn)
      Layer 3: Format check   — URL validity only, recruiter clicks to review manually
    """
    if not url:
        return LinkedInPortfolioAnalysis(notes="No LinkedIn profile detected")

    if not re.search(r"linkedin\.com/(in|pub|company)/", url):
        return LinkedInPortfolioAnalysis(profile_url=url, notes="Invalid LinkedIn URL format")

    # ── Layer 1: Proxycurl API (if key configured) ───────────────────────────
    proxycurl_data = _proxycurl_fetch_linkedin(url)
    if proxycurl_data:
        return _proxycurl_to_linkedin_analysis(
            proxycurl_data, url, jd_required_skills, jd_nice_to_have
        )

    # ── Layer 1.5: Playwright browser scraper ────────────────────────────────
    try:
        playwright_data = asyncio.run(
            asyncio.wait_for(_playwright_scrape_linkedin(url), timeout=18)
        )
    except (asyncio.TimeoutError, Exception):
        playwright_data = None
    if playwright_data and not playwright_data.get("_blocked"):
        return _playwright_data_to_analysis(
            playwright_data, url, jd_required_skills, jd_nice_to_have
        )
    if playwright_data and playwright_data.get("_blocked"):
        # Authwall hit — note it clearly with cookie setup hint
        cookie_hint = (
            "Set LINKEDIN_LI_AT in backend/.env to bypass authwall. "
            "Get it: Chrome → linkedin.com → DevTools (F12) → "
            "Application → Cookies → linkedin.com → copy li_at value"
            if not LINKEDIN_LI_AT
            else "Authwall hit even with cookie — cookie may be expired, refresh it"
        )
        return LinkedInPortfolioAnalysis(
            verified=True,
            profile_url=url,
            verification_score=3.0,
            notes=f"LinkedIn URL verified · authwall blocked scraper · {cookie_hint}",
        )

    def _build_linkedin_cookie_header() -> str:
        """
        Best-effort cookie header for LinkedIn HTML fetch.
        Note: This does not guarantee bypassing authwall, but improves success for
        self-hosted demos where the user provides their own session cookies.
        """
        parts: List[str] = []
        if LINKEDIN_LI_AT:
            parts.append(f"li_at={LINKEDIN_LI_AT}")
        if LINKEDIN_JSESSIONID:
            jsessionid = LINKEDIN_JSESSIONID.strip('"')
            # LinkedIn expects quotes in the cookie value for JSESSIONID
            if not jsessionid.startswith('"'):
                jsessionid = f'"{jsessionid}"'
            parts.append(f"JSESSIONID={jsessionid}")
        return "; ".join(parts)

    def _extract_public_profile_signals(html: str) -> Dict[str, Any]:
        """
        Extract usable signals from a LinkedIn HTML page without relying on brittle selectors.
        """
        soup = BeautifulSoup(html, "html.parser")

        # Prefer OpenGraph/meta signals when present.
        def _meta(prop: str) -> str:
            tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
            return normalize_text(tag.get("content", "")) if tag else ""

        og_title = _meta("og:title")
        og_desc = _meta("og:description") or _meta("description")

        page_title = normalize_text(soup.title.text) if soup.title else ""
        headline = og_desc or og_title or page_title

        # Attempt JSON-LD (often present on public pages)
        full_name = ""
        try:
            for s in soup.find_all("script", attrs={"type": "application/ld+json"}):
                raw = (s.string or "").strip()
                if not raw:
                    continue
                data = json.loads(raw)
                if isinstance(data, dict):
                    full_name = full_name or (data.get("name") or "")
        except Exception:
            pass

        text_blob = normalize_text(soup.get_text(" ", strip=True)).lower()
        return {
            "page_title": page_title,
            "headline": headline,
            "full_name": normalize_text(full_name) if full_name else "",
            "text_blob": text_blob,
        }

    # ── Layer 2: Public HTML scrape (best-effort) ─────────────────────────────
    li_headers = {
        **_BROWSER_HEADERS,
        "Referer": "https://www.google.com/",
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "cross-site",
    }
    try:
        cookie_header = _build_linkedin_cookie_header()
        if cookie_header:
            li_headers["Cookie"] = cookie_header

        response = requests.get(url, headers=li_headers, timeout=5, allow_redirects=True)
        final_url = str(response.url)
        blocked = (
            response.status_code in {401, 999}
            or any(k in final_url for k in ("authwall", "/login", "checkpoint", "uas/login"))
        )

        if not blocked:
            signals = _extract_public_profile_signals(response.text)
            text_blob = signals["text_blob"]

            jd_terms = sorted(set([*jd_required_skills, *jd_nice_to_have]))
            jd_keywords_found = [t for t in jd_terms if t.lower() in text_blob]
            projects_found = len(re.findall(r"\bproject(s)?\b", text_blob))
            connections_count = extract_linkedin_connections(text_blob)
            total_experience_years = extract_experience_years(text_blob)

            score = 0.0
            if jd_keywords_found:       score += 10
            if projects_found > 0:      score += 8
            if connections_count >= 500: score += 5

            cookie_note = (
                "✅ Fetched with cookies (li_at/JSESSIONID)"
                if cookie_header
                else "⚠️ Fetched without cookies (public/partial)"
            )
            return LinkedInPortfolioAnalysis(
                verified=True,
                profile_url=url,
                headline=signals.get("headline", ""),
                current_title=signals.get("page_title", ""),
                total_experience_years=total_experience_years,
                projects_found=projects_found,
                jd_keywords_found=jd_keywords_found,
                connections_count=connections_count,
                verification_score=round(score, 2),
                notes=f"LinkedIn HTML parsed ({cookie_note}) — add PROXYCURL_API_KEY for full profile",
            )
    except Exception as exc:
        logger.debug("LinkedIn public fetch failed for %s: %s", url, exc)

    # ── Layer 3: Format-verified fallback ─────────────────────────────────────
    proxycurl_hint = (
        "Add PROXYCURL_API_KEY to backend/.env for full profile data "
        "(free tier: https://nubela.co/proxycurl)"
        if not PROXYCURL_API_KEY
        else "Proxycurl returned no data for this profile"
    )
    return LinkedInPortfolioAnalysis(
        verified=True,
        profile_url=url,
        verification_score=5.0,
        notes=f"LinkedIn URL format verified · click to view manually · {proxycurl_hint}",
    )


def github_api_get_json(endpoint: str, *, _retry_without_token: bool = True) -> Dict[str, Any]:
    """
    Lightweight GitHub REST wrapper.

    Important: if a configured GITHUB_TOKEN is invalid/expired, we automatically retry once
    without auth so repo scanning still works (with lower rate limits) instead of hard-failing.
    """

    def _build_headers(include_auth: bool) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "PIXLS-resume-screener/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if include_auth and GITHUB_TOKEN:
            headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
        return headers

    try:
        response = requests.get(
            f"https://api.github.com{endpoint}",
            headers=_build_headers(include_auth=True),
            timeout=10,
        )
    except Exception as exc:
        logger.warning("GitHub API request failed (%s): %s", endpoint, exc)
        return {"ok": False, "status_code": None, "data": None, "error": str(exc)}

    # Rate limit hit
    if response.status_code == 403:
        remaining = response.headers.get("X-RateLimit-Remaining", "?")
        reset_ts = response.headers.get("X-RateLimit-Reset", "?")
        msg = response.json().get("message", "") if response.content else ""
        if "rate limit" in msg.lower() or remaining == "0":
            logger.warning(
                "GitHub API rate limit hit (remaining=%s, reset=%s). "
                "Set GITHUB_TOKEN in backend/.env for 5000 req/hr.",
                remaining, reset_ts,
            )
            return {"ok": False, "status_code": 403, "data": None,
                    "error": "rate_limit_exceeded"}
        return {"ok": False, "status_code": 403, "data": None, "error": msg}

    if response.status_code == 401:
        # If we *have* a token configured, treat this as "token bad" and retry once without it.
        if GITHUB_TOKEN and _retry_without_token:
            logger.warning(
                "GitHub API: bad credentials (token invalid/expired). Retrying without token."
            )
            try:
                response = requests.get(
                    f"https://api.github.com{endpoint}",
                    headers=_build_headers(include_auth=False),
                    timeout=10,
                )
            except Exception as exc:
                return {"ok": False, "status_code": None, "data": None, "error": str(exc)}

            if response.status_code == 401:
                return {
                    "ok": False,
                    "status_code": 401,
                    "data": None,
                    "error": "bad_credentials",
                }
        else:
            logger.warning("GitHub API: bad credentials (token invalid or expired)")
            return {"ok": False, "status_code": 401, "data": None, "error": "bad_credentials"}

    if response.status_code >= 400:
        return {"ok": False, "status_code": response.status_code, "data": None}

    try:
        return {
            "ok": True,
            "status_code": response.status_code,
            "data": response.json(),
            "headers": dict(response.headers),
        }
    except Exception:
        return {"ok": False, "status_code": response.status_code, "data": None}


def analyze_github_repository(
    owner: str,
    repo_data: Dict[str, Any],
    jd_required_skills: List[str],
    jd_nice_to_have: List[str],
) -> ProjectVerification:
    repo_name = repo_data.get("name", "unknown-repo")
    repo_topics = [topic.lower() for topic in repo_data.get("topics", [])]
    repo_description = repo_data.get("description") or ""
    repo_size = int(repo_data.get("size") or 0)
    repo_html_url = repo_data.get("html_url") or f"https://github.com/{owner}/{repo_name}"

    languages_payload = github_api_get_json(f"/repos/{owner}/{repo_name}/languages")
    languages = languages_payload.get("data") or {}

    # Fetch README preview (first 400 chars of decoded content)
    readme_preview = ""
    try:
        readme_payload = github_api_get_json(f"/repos/{owner}/{repo_name}/readme")
        if readme_payload.get("ok") and readme_payload.get("data"):
            encoded = readme_payload["data"].get("content", "")
            if encoded:
                decoded = base64.b64decode(encoded.replace("\n", "")).decode("utf-8", errors="ignore")
                # Strip markdown headers/badges for clean preview
                clean = re.sub(r"!\[.*?\]\(.*?\)", "", decoded)
                clean = re.sub(r"\[.*?\]\(.*?\)", lambda m: m.group(0).split("]")[0].lstrip("["), clean)
                clean = re.sub(r"#+\s*", "", clean)
                clean = re.sub(r"\s+", " ", clean).strip()
                readme_preview = clean[:400].strip()
    except Exception:
        pass

    contributors_count = 2 if int(repo_data.get("forks_count") or 0) > 0 else 1

    readme_text = " ".join(
        [
            repo_description.lower(),
            " ".join(repo_topics),
            " ".join([language.lower() for language in languages.keys()]),
        ]
    )

    root_names = [
        "dockerfile" if "docker" in readme_text else "",
        "tests" if "test" in readme_text else "",
        ".github" if "github actions" in readme_text or "workflow" in readme_text else "",
    ]
    root_names = [name for name in root_names if name]

    tests_present = "tests" in root_names or "pytest" in readme_text or "jest" in readme_text
    test_file_count = 2 if tests_present else 0
    deployment_ready = (
        "dockerfile" in root_names
        or "kubernetes" in readme_text
        or "ci/cd" in readme_text
        or "github actions" in readme_text
    )
    env_configs_count = 2 if ".env" in readme_text else 0

    last_commit = repo_data.get("pushed_at")
    status = get_activity_status(last_commit)
    recent_commits = status in {"Active", "Recent"}
    weekly_activity = status == "Active"

    estimated_file_count = max(1, int(repo_size / 4))
    repo_tech_stack = infer_repo_tech_stack(
        repo_name=repo_name,
        repo_description=repo_description,
        repo_topics=repo_topics,
        languages=languages,
        readme_text=readme_text,
        root_file_names=root_names,
    )

    all_jd_skills = sorted(list(set([*jd_required_skills, *jd_nice_to_have])))
    coverage_info = calculate_jd_stack_coverage(languages, repo_tech_stack, all_jd_skills)
    complexity_score = calculate_complexity_score(
        stars=int(repo_data.get("stargazers_count") or 0),
        forks=int(repo_data.get("forks_count") or 0),
        estimated_file_count=estimated_file_count,
        contributors_count=contributors_count,
        tests_present=tests_present,
        test_file_count=test_file_count,
        deployment_ready=deployment_ready,
        env_configs_count=env_configs_count,
        recent_commits=recent_commits,
        weekly_activity=weekly_activity,
    )
    complexity_label = classify_complexity_label(complexity_score)
    project_type = classify_project_type(repo_tech_stack, root_names, estimated_file_count)

    return ProjectVerification(
        repo_name=repo_name,
        repo_url=repo_html_url,
        project_type=project_type,
        complexity_score=complexity_score,
        complexity_label=complexity_label,
        tech_stack=repo_tech_stack,
        jd_matched_tech=coverage_info["matched_skills"],
        jd_stack_coverage_pct=coverage_info["coverage_pct"],
        stars=int(repo_data.get("stargazers_count") or 0),
        forks=int(repo_data.get("forks_count") or 0),
        estimated_file_count=estimated_file_count,
        contributors=contributors_count,
        tests_present=tests_present,
        deployment_ready=deployment_ready,
        last_commit_at=last_commit,
        activity_status=status,
        description=repo_description[:200] if repo_description else "",
        readme_preview=readme_preview,
    )


def verify_github_portfolio(
    github_url: Optional[str], jd_required_skills: List[str], jd_nice_to_have: List[str]
) -> Dict[str, Any]:
    if not github_url:
        return {
            "github_activity": GitHubActivity(notes="No GitHub link detected"),
            "github_analysis": GitHubPortfolioAnalysis(notes="No GitHub link detected"),
        }

    username = extract_github_username(github_url)
    if not username:
        return {
            "github_activity": GitHubActivity(notes="Invalid GitHub URL format"),
            "github_analysis": GitHubPortfolioAnalysis(
                profile_url=github_url,
                notes="Invalid GitHub URL format",
            ),
        }

    jd_key = ",".join(sorted(list(set([*jd_required_skills, *jd_nice_to_have]))))
    cache_key = f"{username}|{jd_key}"
    cached = get_github_cache(cache_key)
    if cached:
        return {
            "github_activity": GitHubActivity(**cached["github_activity"]),
            "github_analysis": GitHubPortfolioAnalysis(**cached["github_analysis"]),
        }

    profile_payload = github_api_get_json(f"/users/{username}")
    if not profile_payload.get("ok"):
        err = profile_payload.get("error", "")
        if err == "rate_limit_exceeded":
            note = (
                "GitHub API rate limit exceeded (60 req/hr unauthenticated). "
                "Add GITHUB_TOKEN to backend/.env for 5000 req/hr — "
                "generate one at https://github.com/settings/tokens"
            )
        elif err == "bad_credentials":
            note = "GitHub token invalid or expired — update GITHUB_TOKEN in backend/.env"
        else:
            note = f"GitHub username not found or API error ({err or profile_payload.get('status_code', 'unknown')})"
        return {
            "github_activity": GitHubActivity(username=username, notes=note),
            "github_analysis": GitHubPortfolioAnalysis(
                profile_url=github_url, username=username, notes=note
            ),
        }

    user_data = profile_payload["data"]
    repos_payload = github_api_get_json(
        f"/users/{username}/repos?sort=updated&per_page={GITHUB_REPO_ANALYZE_LIMIT}"
    )
    repos_data = repos_payload.get("data") if repos_payload.get("ok") else []
    if not isinstance(repos_data, list):
        repos_data = []

    analyzed_projects: List[ProjectVerification] = []
    for repo_data in repos_data:
        if repo_data.get("fork"):
            continue
        try:
            analyzed_projects.append(
                analyze_github_repository(
                    owner=username,
                    repo_data=repo_data,
                    jd_required_skills=jd_required_skills,
                    jd_nice_to_have=jd_nice_to_have,
                )
            )
        except Exception:
            continue

    analyzed_projects.sort(
        key=lambda item: (item.jd_stack_coverage_pct, item.complexity_score), reverse=True
    )
    top_projects = analyzed_projects[:3]

    jd_relevant_projects = len(
        [
            project
            for project in analyzed_projects
            if project.jd_stack_coverage_pct >= 20
            and project.activity_status in {"Active", "Recent"}
        ]
    )
    stack_coverage_pct = round(
        sum([project.jd_stack_coverage_pct for project in analyzed_projects])
        / max(1, len(analyzed_projects)),
        2,
    )
    best_complexity = round(
        max([project.complexity_score for project in analyzed_projects], default=0.0), 2
    )
    activity_status = (
        "Active"
        if any(project.activity_status == "Active" for project in analyzed_projects)
        else "Recent"
        if any(project.activity_status == "Recent" for project in analyzed_projects)
        else "Stale"
    )

    github_score = 0.0
    if activity_status in {"Active", "Recent"}:
        github_score += 10
    if jd_relevant_projects >= 2:
        github_score += 15
    if stack_coverage_pct > 50:
        github_score += 20
    if best_complexity >= 7:
        github_score += 10
    if any(project.contributors >= 2 for project in analyzed_projects):
        github_score += 5

    language_counter = Counter()
    for project in analyzed_projects:
        for tech in project.tech_stack:
            language_counter[tech] += 1

    latest_commit = max(
        [project.last_commit_at for project in analyzed_projects if project.last_commit_at],
        default=None,
    )

    github_activity = GitHubActivity(
        valid=True,
        username=username,
        repo_count=int(user_data.get("public_repos") or 0),
        last_active=latest_commit,
        top_languages=[lang for lang, _ in language_counter.most_common(3)],
        bonus_points=round(github_score, 2),
        notes="GitHub profile and projects analyzed",
    )

    github_analysis = GitHubPortfolioAnalysis(
        verified=True,
        profile_url=github_url,
        username=username,
        total_public_repos=int(user_data.get("public_repos") or 0),
        repos_analyzed=len(analyzed_projects),
        jd_relevant_projects=jd_relevant_projects,
        stack_coverage_pct=stack_coverage_pct,
        best_project_complexity=best_complexity,
        activity_status=activity_status,
        verification_score=round(github_score, 2),
        top_projects=top_projects,
        notes="GitHub smart verification completed",
    )

    payload = {
        "github_activity": github_activity.model_dump(),
        "github_analysis": github_analysis.model_dump(),
    }
    set_github_cache(cache_key, payload)

    return {
        "github_activity": github_activity,
        "github_analysis": github_analysis,
    }


def build_smart_portfolio_summary(
    github_analysis: GitHubPortfolioAnalysis, linkedin_analysis: LinkedInPortfolioAnalysis
) -> SmartPortfolioSummary:
    github_raw = github_analysis.verification_score
    linkedin_raw = linkedin_analysis.verification_score
    weighted_raw = (github_raw * 0.7) + (linkedin_raw * 0.3)
    scale_factor = 65.0 / 49.8
    verification_bonus = round(min(65.0, weighted_raw * scale_factor), 2)

    stack_verified = (
        github_analysis.jd_relevant_projects >= 1
        and github_analysis.stack_coverage_pct >= 20
        and github_analysis.activity_status in {"Active", "Recent"}
    )

    hr_insight = (
        "Strong portfolio with verified JD-relevant projects and active coding profile."
        if stack_verified and github_analysis.best_project_complexity >= 7
        else "Portfolio partially verified. Consider reviewing project depth manually."
        if github_analysis.verified
        else "Portfolio could not be strongly verified from public data."
    )

    return SmartPortfolioSummary(
        github_score=round(github_raw, 2),
        linkedin_score=round(linkedin_raw, 2),
        verification_bonus=verification_bonus,
        stack_experience_verified=stack_verified,
        hr_insight=hr_insight,
    )


def extract_text_from_pdf(file_bytes: bytes) -> str:
    text_chunks: List[str] = []
    reader = PdfReader(io.BytesIO(file_bytes))
    for page in reader.pages:
        text_chunks.append(page.extract_text() or "")
    return normalize_text(" ".join(text_chunks))


def extract_text_from_docx(file_bytes: bytes) -> str:
    doc = Document(io.BytesIO(file_bytes))
    return normalize_text(" ".join([para.text for para in doc.paragraphs]))


def extract_text_from_txt(file_bytes: bytes) -> str:
    return normalize_text(file_bytes.decode("utf-8", errors="ignore"))


def extract_text_by_extension(filename: str, file_bytes: bytes) -> str:
    lower_name = filename.lower()
    if lower_name.endswith(".pdf"):
        return extract_text_from_pdf(file_bytes)
    if lower_name.endswith(".docx"):
        return extract_text_from_docx(file_bytes)
    if lower_name.endswith(".txt"):
        return extract_text_from_txt(file_bytes)
    return ""


def extract_links_by_extension(filename: str, file_bytes: bytes, extracted_text: str) -> List[str]:
    lower_name = filename.lower()
    inferred_links = infer_social_urls_from_text(extracted_text)

    if lower_name.endswith(".pdf"):
        annotation_links = extract_pdf_annotation_links(file_bytes)

        # OCR fallback only if GitHub is still missing (for button/image-based links)
        needs_ocr = not any(
            "github.com" in link.lower() for link in [*inferred_links, *annotation_links]
        )
        ocr_links: List[str] = []
        if needs_ocr:
            ocr_text = extract_pdf_ocr_text(file_bytes)
            ocr_links = infer_social_urls_from_text(ocr_text)

        return unique_links([*inferred_links, *annotation_links, *ocr_links])

    return unique_links(inferred_links)


def extract_resumes_from_zip(zip_bytes: bytes) -> List[Dict[str, Any]]:
    resumes: List[Dict[str, Any]] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zip_ref:
        for file_name in zip_ref.namelist():
            if file_name.endswith("/"):
                continue
            if not file_name.lower().endswith((".pdf", ".docx", ".txt")):
                continue
            with zip_ref.open(file_name) as resume_file:
                raw_bytes = resume_file.read()
                extracted_text = extract_text_by_extension(file_name, raw_bytes)
                if extracted_text:
                    extracted_links = extract_links_by_extension(
                        file_name, raw_bytes, extracted_text
                    )

                    # Determine extraction method and apply Groq fallback if needed
                    has_github = any("github.com" in lnk.lower() for lnk in extracted_links)
                    github_extraction_method = "rule-based"
                    if not has_github and _groq_client:
                        groq_links = hybrid_extractor.groq_github_fallback(extracted_text)
                        if groq_links:
                            extracted_links = unique_links([*extracted_links, *groq_links])
                            github_extraction_method = "llm-groq"
                            logger.info("Groq fallback used for %s", file_name)

                    merged_text = normalize_text(
                        f"{extracted_text} {' '.join(extracted_links)}"
                    )
                    candidate_name = extract_candidate_name(raw_bytes, file_name, extracted_text)
                    candidate_email = extract_candidate_email(extracted_text)
                    resumes.append(
                        {
                            "candidate_name": candidate_name,
                            "candidate_email": candidate_email,
                            "source_file": Path(file_name).name,
                            "text": merged_text,
                            "extracted_links": extracted_links,
                            "github_extraction_method": github_extraction_method,
                            "file_bytes": raw_bytes,
                        }
                    )
    return resumes


_EMAIL_REGEX = re.compile(
    r"(?<![/\\@])"            # not preceded by /, \, or @ (avoids partial URL matches)
    r"[a-zA-Z0-9._%+\-]{1,64}"
    r"@"
    r"[a-zA-Z0-9.\-]{1,253}"
    r"\.[a-zA-Z]{2,10}"
    r"(?![.\-@\w])",          # not followed by more domain-like chars
    re.IGNORECASE,
)

# Common placeholder / example domains to skip
_EMAIL_IGNORE_DOMAINS = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com",  # kept — real candidates use these
}
_EMAIL_SKIP_PATTERNS = re.compile(
    r"(example|sample|test|noreply|no-reply|support|info|contact|admin|foo|bar)\b",
    re.IGNORECASE,
)


def extract_candidate_email(text: str) -> Optional[str]:
    """Return the most likely personal contact email from raw resume text.

    Strategy:
    1. Find all valid email addresses in the text.
    2. Prefer the first one that does NOT look like a placeholder/service address.
    3. Fall back to the very first match if all look like placeholders.
    """
    matches = _EMAIL_REGEX.findall(text)
    if not matches:
        return None

    candidates: List[str] = []
    for m in matches:
        m = m.strip(".,;:)")
        if not _EMAIL_SKIP_PATTERNS.search(m.split("@")[0]):
            candidates.append(m)

    return candidates[0] if candidates else matches[0].strip(".,;:)")


# ─────────────────────────────────────────────────────────────────────────────
# CANDIDATE NAME EXTRACTION — 4-layer pipeline
# ─────────────────────────────────────────────────────────────────────────────

# Words that commonly appear in resume filenames but are NOT part of the person's name
_NAME_FILENAME_STRIP = re.compile(
    r"[\-_]?(resume|cv|curriculum|vitae|updated|new|final|v\d|202\d|2019|2018)[\-_]?",
    re.IGNORECASE,
)

# Tokens that indicate we've passed the name section
_CONTACT_SIGNAL = re.compile(
    r"@|\bphone\b|\bmobile\b|\bemail\b|\baddress\b|\blinkedin\b|\bgithub\b"
    r"|\bportfolio\b|\bwebsite\b|\btel\b|\bfax\b|\+\d|\(\d{3}\)",
    re.IGNORECASE,
)

# Reject lines that are clearly not names
_NON_NAME_PATTERN = re.compile(
    r"\d{4,}|http|www\.|\.com|\.io|@|summary|objective|profile|education"
    r"|experience|skills|projects|certifications|languages|interests|hobbies",
    re.IGNORECASE,
)


def _looks_like_name(text: str) -> bool:
    """Return True if text plausibly is a person's full name."""
    text = text.strip()
    if not text or len(text) < 3 or len(text) > 55:
        return False
    words = text.split()
    if len(words) < 1 or len(words) > 6:
        return False
    if any(c.isdigit() for c in text):
        return False
    if re.search(r"[^a-zA-Z\s\-\'\.]", text):
        return False
    skip = {"resume", "curriculum", "vitae", "cv", "profile", "summary",
            "objective", "candidate", "applicant", "contact"}
    if text.lower().strip() in skip:
        return False
    # At least one word must start with uppercase (proper noun signal)
    if not any(w[0].isupper() for w in words if w):
        return False
    return True


def _name_from_pdf_fonts(raw_bytes: bytes) -> Optional[str]:
    """Layer 1 — find the largest-font text on page 1 of a PDF."""
    try:
        with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
            if not pdf.pages:
                return None
            page = pdf.pages[0]
            words = page.extract_words(extra_attrs=["size"])
            if not words:
                return None

            # Find maximum font size
            max_size = max((w.get("size") or 0) for w in words)
            if max_size <= 0:
                return None

            # Collect words rendered at the maximum (or within 1.5 pt) font size,
            # stopping as soon as we leave that size run
            name_words: List[str] = []
            for w in words:
                size = w.get("size") or 0
                if size >= max_size - 1.5:
                    name_words.append(w["text"])
                elif name_words:
                    # First size-drop after collecting words — stop here
                    break

            candidate = " ".join(name_words).strip()
            if _looks_like_name(candidate):
                return candidate.title()
    except Exception:
        pass
    return None


def _name_from_text_heuristic(text: str) -> Optional[str]:
    """Layer 2 — first clean, name-like line near the top of the document."""
    lines = [ln.strip() for ln in text[:1200].splitlines() if ln.strip()]
    for line in lines[:15]:
        if _CONTACT_SIGNAL.search(line):
            continue
        if _NON_NAME_PATTERN.search(line):
            continue
        # Ignore lines with too many words (address/title) or too few (single initial)
        words = line.split()
        if len(words) < 2 or len(words) > 5:
            continue
        if _looks_like_name(line):
            return line.title()
    return None


def _name_from_regex(text: str) -> Optional[str]:
    """Layer 3 — regex patterns common in professionally formatted resumes."""
    patterns = [
        # Explicit "Name:" field
        r"(?:^|\n)\s*[Nn]ame\s*[:\-]\s*([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){1,4})",
        # Name immediately followed by email on the next line
        r"^([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){1,4})\s*\n\s*[a-zA-Z0-9._%+\-]+@",
        # Name immediately followed by phone
        r"^([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){1,4})\s*\n\s*[\+\(]?\d",
        # All-caps name (common in older resumes)
        r"^([A-Z]{2,}(?:\s+[A-Z]{2,}){1,4})\s*\n",
    ]
    for pattern in patterns:
        m = re.search(pattern, text[:2500], re.MULTILINE)
        if m:
            raw = m.group(1).strip()
            # Convert ALL-CAPS to Title Case
            candidate = raw.title()
            if _looks_like_name(candidate):
                return candidate
    return None


def _name_from_groq(text: str) -> Optional[str]:
    """Layer 4 — Groq LLM fallback (only called if other layers fail)."""
    if not _groq_client:
        return None
    try:
        resp = _groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{
                "role": "user",
                "content": (
                    "Extract the full name of the job applicant from this resume. "
                    "Return ONLY the name — no punctuation, no explanation.\n\n"
                    f"{text[:1800]}"
                ),
            }],
            max_tokens=15,
            temperature=0,
        )
        raw = resp.choices[0].message.content.strip().strip('"\'').strip()
        candidate = raw.title()
        if _looks_like_name(candidate):
            logger.info("Name extracted via Groq LLM: %s", candidate)
            return candidate
    except Exception as exc:
        logger.debug("Groq name extraction failed: %s", exc)
    return None


def _name_from_filename(file_name: str) -> str:
    """Layer 5 — smart filename cleanup (always succeeds)."""
    stem = Path(file_name).stem
    cleaned = _NAME_FILENAME_STRIP.sub(" ", stem).strip()
    cleaned = cleaned.replace("_", " ").replace("-", " ")
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned.title() if cleaned else stem.title()


def extract_candidate_name(raw_bytes: bytes, file_name: str, text: str) -> str:
    """
    Multi-layer candidate name extractor.
    Tries layers in order and returns the first confident result.
    Always returns something (fallback to filename).
    """
    is_pdf = file_name.lower().endswith(".pdf")

    # Layer 1: PDF font analysis (most reliable for well-formatted resumes)
    if is_pdf and raw_bytes:
        name = _name_from_pdf_fonts(raw_bytes)
        if name:
            logger.debug("Name via fonts: %s ← %s", name, file_name)
            return name

    # Layer 2: Top-of-document heuristic
    name = _name_from_text_heuristic(text)
    if name:
        logger.debug("Name via heuristic: %s ← %s", name, file_name)
        return name

    # Layer 3: Regex patterns
    name = _name_from_regex(text)
    if name:
        logger.debug("Name via regex: %s ← %s", name, file_name)
        return name

    # Layer 4: Groq LLM (expensive — only for edge cases)
    name = _name_from_groq(text)
    if name:
        return name

    # Layer 5: Filename fallback
    name = _name_from_filename(file_name)
    logger.debug("Name via filename fallback: %s ← %s", name, file_name)
    return name


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 1 — ATS COMPATIBILITY SCORER
# ─────────────────────────────────────────────────────────────────────────────

_ATS_SECTION_HEADERS = ["education", "experience", "skills", "projects", "summary", "objective", "certifications"]
_ATS_BUZZWORDS_PENALISED = [
    "synergy", "leverage", "paradigm", "proactive", "go-getter", "team player",
    "results-driven", "detail-oriented", "thought leader", "dynamic",
]


def compute_ats_score(text: str, file_bytes: Optional[bytes] = None, filename: str = "") -> ATSScore:
    score = 100
    issues: List[str] = []
    suggestions: List[str] = []
    text_lower = text.lower()

    # Table detection via pdfplumber
    if filename.lower().endswith(".pdf") and file_bytes:
        try:
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                for page in pdf.pages:
                    if page.extract_tables():
                        score -= 25
                        issues.append("Table layout detected")
                        suggestions.append("Replace tables with plain text sections (+25% ATS)")
                        break
        except Exception:
            pass

    # Missing section headers
    found = [h for h in _ATS_SECTION_HEADERS if h in text_lower]
    missing = [h for h in _ATS_SECTION_HEADERS[:5] if h not in text_lower]
    if len(missing) >= 3:
        score -= 10
        issues.append(f"Missing sections: {', '.join(missing[:3])}")
        suggestions.append(f"Add {missing[0].title()} section (+10% ATS)")
    elif len(missing) >= 1:
        suggestions.append(f"Consider adding {missing[0].title()} section")

    # Keyword density
    words = text.split()
    word_count = max(len(words), 1)
    skill_hits = sum(1 for w in words if w.lower() in SKILL_DICTIONARY)
    density_pct = skill_hits / word_count * 100
    if 2 <= density_pct <= 5:
        score += 10
        score = min(score, 100)
    elif density_pct < 1:
        score -= 8
        issues.append("Low keyword density (<1%)")
        suggestions.append("Increase relevant keyword density to 2–5%")

    # Excessive buzzwords
    buzz_count = sum(1 for bw in _ATS_BUZZWORDS_PENALISED if bw in text_lower)
    if buzz_count >= 3:
        score -= 8
        issues.append(f"Overused buzzwords ({buzz_count} found)")
        suggestions.append("Replace vague buzzwords with specific achievements")

    # Very short resume (likely image-only)
    if word_count < 80:
        score -= 20
        issues.append("Very little text extracted (possible image-based PDF)")
        suggestions.append("Use a text-based resume format for better ATS parsing")

    score = max(0, min(100, score))
    label = "Green" if score >= 75 else "Orange" if score >= 50 else "Red"
    return ATSScore(score=score, label=label, issues=issues, suggestions=suggestions)


def evaluate_timeline_consistency(text: str) -> Dict[str, Any]:
    text_lower = text.lower()
    grad_years = re.findall(
        r"(?:graduated|batch|passed out|class of)\s*[:\-]?\s*(20\d{2}|19\d{2})",
        text_lower,
    )
    work_years = re.findall(r"(20\d{2})\s*[-–]\s*(?:present|current)", text_lower)
    if grad_years and work_years:
        try:
            grad_year = max(int(y) for y in grad_years)
            work_start = min(int(y) for y in work_years)
            if work_start < grad_year - 1:
                return {
                    "passed": False,
                    "detail": f"Potential mismatch: work start {work_start}, graduation {grad_year}",
                }
        except Exception:
            pass
    return {"passed": True, "detail": "No mismatch detected"}


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 5a — NOTABLE ACHIEVEMENTS
# ─────────────────────────────────────────────────────────────────────────────

_HACKATHON_PATTERNS = re.compile(
    r"\b(hackathon|hack[\-\s]?a[\-\s]?thon|hackfest|hack\s*day|techfest|"
    r"finalist|1st\s+place|2nd\s+place|3rd\s+place|winner|runner[\-\s]?up|"
    r"ktu\s+fest|ieee|acm|national\s+level|state\s+level|"
    r"smart\s*india\s*hackathon|sih|coding\s+competi|code\s+sprint|"
    r"build[a]?thon|inno[\s\-]?fest)\b",
    re.IGNORECASE,
)

_CERT_PATTERNS = re.compile(
    r"\b(aws\s+certified|google\s+cloud|azure\s+certified|"
    r"coursera|udemy|nptel|oracle\s+certified|cisco|"
    r"certified\s+\w+\s+\w*|professional\s+certificate|"
    r"specialization|microsoft\s+certified|meta\s+front[\-\s]?end)\b",
    re.IGNORECASE,
)


def _calculate_achievement_score(ach: dict) -> float:
    score = 0.0
    score += ach.get("stars", 0) * 3
    score += ach.get("forks", 0) * 2
    if ach.get("deployed_url"):
        score += 15
    atype = ach.get("achievement_type", "")
    if atype == "hackathon":
        score += 20
    elif atype == "certification":
        score += 10
    elif atype == "linkedin":
        score += 8
    return round(min(100.0, score), 1)


def _medal(score: float) -> str:
    if score >= 50:
        return "🥇"
    if score >= 25:
        return "🥈"
    return "🥉"


def _extract_hackathons_from_text(text: str) -> List[dict]:
    """Scan resume text for hackathon / competition mentions."""
    items: List[dict] = []
    seen: set = set()
    for match in _HACKATHON_PATTERNS.finditer(text):
        start = max(0, match.start() - 60)
        end = min(len(text), match.end() + 60)
        snippet = text[start:end].strip().replace("\n", " ")
        key = match.group(0).lower()
        if key not in seen:
            seen.add(key)
            items.append(
                {
                    "title": snippet[:80],
                    "achievement_type": "hackathon",
                    "stars": 0,
                    "forks": 0,
                    "deployed_url": None,
                    "url": None,
                    "description": snippet,
                }
            )
    return items[:4]


def _extract_certifications_from_text(text: str) -> List[dict]:
    """Scan resume text for certification mentions."""
    items: List[dict] = []
    seen: set = set()
    for match in _CERT_PATTERNS.finditer(text):
        start = max(0, match.start() - 20)
        end = min(len(text), match.end() + 60)
        snippet = text[start:end].strip().replace("\n", " ")
        key = match.group(0).lower()
        if key not in seen:
            seen.add(key)
            items.append(
                {
                    "title": snippet[:80],
                    "achievement_type": "certification",
                    "stars": 0,
                    "forks": 0,
                    "deployed_url": None,
                    "url": None,
                    "description": snippet,
                }
            )
    return items[:4]


def analyze_notable_achievements(
    resume_text: str,
    github_analysis: "GitHubPortfolioAnalysis",
    linkedin_analysis: "LinkedInPortfolioAnalysis",
) -> "NotableAchievements":
    """
    Aggregate achievements from 4 sources, score, rank, return top 5.
    Sources: GitHub repos · LinkedIn achievements/certs · Resume hackathons · Resume certifications
    """
    raw: List[dict] = []

    # ── Source 1: GitHub top projects ──
    for proj in github_analysis.top_projects:
        raw.append(
            {
                "title": proj.repo_name,
                "achievement_type": "github",
                "stars": proj.stars,
                "forks": proj.forks,
                "deployed_url": proj.repo_url if proj.deployment_ready else None,
                "url": proj.repo_url,
                "description": proj.description or proj.readme_preview,
            }
        )

    # ── Source 2: LinkedIn achievements ──
    for ach in (linkedin_analysis.achievements or []):
        raw.append(
            {
                "title": ach[:80],
                "achievement_type": "linkedin",
                "stars": 0,
                "forks": 0,
                "deployed_url": None,
                "url": linkedin_analysis.profile_url,
                "description": ach,
            }
        )

    # ── Source 3: Resume – hackathons / competitions ──
    raw.extend(_extract_hackathons_from_text(resume_text))

    # ── Source 4: Resume – certifications ──
    raw.extend(_extract_certifications_from_text(resume_text))

    # Score + deduplicate (by lowercased title prefix)
    seen_titles: set = set()
    scored: List[dict] = []
    for item in raw:
        key = item["title"].lower()[:30]
        if key in seen_titles:
            continue
        seen_titles.add(key)
        item["score"] = _calculate_achievement_score(item)
        item["medal"] = _medal(item["score"])
        scored.append(item)

    scored.sort(key=lambda x: x["score"], reverse=True)
    top5 = scored[:5]

    achievements = [
        NotableAchievement(
            title=a["title"],
            achievement_type=a["achievement_type"],
            score=a["score"],
            stars=a.get("stars", 0),
            forks=a.get("forks", 0),
            deployed_url=a.get("deployed_url"),
            url=a.get("url"),
            description=a.get("description", ""),
            medal=a["medal"],
        )
        for a in top5
    ]

    return NotableAchievements(top_achievements=achievements, total_found=len(scored))


def calculate_verification_summary(
    resume_text: str,
    github_analysis: "GitHubPortfolioAnalysis",
    linkedin_analysis: "LinkedInPortfolioAnalysis",
    scanned_links: List["LinkScanResult"],
    portfolio_reachable: bool,
) -> "VerificationSummary":
    timeline = evaluate_timeline_consistency(resume_text)
    checks = [
        VerificationCheck(
            name="GitHub linked",
            passed=bool(github_analysis.verified and github_analysis.username),
            detail=github_analysis.profile_url or "No GitHub profile detected",
        ),
        VerificationCheck(
            name="LinkedIn linked",
            passed=bool(linkedin_analysis.verified and linkedin_analysis.profile_url),
            detail=linkedin_analysis.profile_url or "No LinkedIn profile detected",
        ),
        VerificationCheck(
            name="Portfolio reachable",
            passed=portfolio_reachable,
            detail="Reachable" if portfolio_reachable else "Not reachable",
        ),
        VerificationCheck(
            name="Links scanned",
            passed=len(scanned_links) > 0,
            detail=f"{len(scanned_links)} link(s) scanned",
        ),
        VerificationCheck(
            name="Timeline consistency",
            passed=bool(timeline["passed"]),
            detail=str(timeline["detail"]),
        ),
    ]
    checks_passed = sum(1 for c in checks if c.passed)
    if checks_passed >= 4:
        status = "verified_high"
    elif checks_passed >= 2:
        status = "verified_partially"
    else:
        status = "verification_risk"
    return VerificationSummary(
        status=status,
        checks_passed=checks_passed,
        checks_total=len(checks),
        checks=checks,
    )


def determine_shortlist_recommendation(
    fit_score: float,
    missing_skills: List[str],
    verification_summary: VerificationSummary,
) -> Literal["Advance", "Review", "Hold"]:
    if fit_score >= 80 and len(missing_skills) <= 1 and verification_summary.checks_passed >= 4:
        return "Advance"
    if fit_score < 60 or len(missing_skills) >= 4 or verification_summary.checks_passed <= 1:
        return "Hold"
    return "Review"


def build_evidence_summary(
    matched_skills: List[str],
    missing_skills: List[str],
    github_analysis: GitHubPortfolioAnalysis,
    linkedin_analysis: LinkedInPortfolioAnalysis,
) -> str:
    parts: List[str] = []
    if matched_skills:
        parts.append(f"Matched skills: {', '.join(matched_skills[:4])}")
    if missing_skills:
        parts.append(f"Missing: {', '.join(missing_skills[:3])}")
    if github_analysis.verified:
        parts.append(
            f"GitHub verified ({github_analysis.jd_relevant_projects} JD-relevant project(s), {github_analysis.activity_status.lower()} activity)"
        )
    if linkedin_analysis.verified:
        parts.append("LinkedIn profile verified")
    return " | ".join(parts) if parts else "Limited evidence available from resume and links"


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 7 — BULK EMAIL TEMPLATES
# ─────────────────────────────────────────────────────────────────────────────

def generate_email_template(
    candidate_name: str,
    fit_score: float,
    matched_skills: List[str],
    missing_skills: List[str],
    github_username: Optional[str],
) -> EmailTemplates:
    first_name = candidate_name.split()[0] if candidate_name else "Candidate"
    top_skill = matched_skills[0].title() if matched_skills else "your skills"
    gap_skill = missing_skills[0].title() if missing_skills else None
    gh_note = f" Your GitHub profile ({github_username}) shows strong project work." if github_username else ""

    if fit_score >= 80:
        template_type = "advance"
        subject = f"Interview Invitation — {candidate_name}"
        body = (
            f"Hi {first_name},\n\n"
            f"We were impressed by your profile — particularly your expertise in {top_skill}.{gh_note}\n\n"
            f"We'd love to invite you for a technical interview. Please reply with your availability "
            f"for the next 5 business days.\n\n"
            f"Best regards,\nHR Team"
        )
    elif fit_score >= 60:
        template_type = "waitlist"
        subject = f"Application Update — {candidate_name}"
        body = (
            f"Hi {first_name},\n\n"
            f"Thank you for applying. Your {top_skill} background is strong and we have added you "
            f"to our talent pipeline for upcoming roles.{gh_note}\n\n"
            f"We will reach out when a matching position opens. Stay in touch!\n\n"
            f"Best regards,\nHR Team"
        )
    else:
        template_type = "reject"
        gap_line = f" We noticed a gap in {gap_skill}, which is core to this role." if gap_skill else ""
        strength_note = f"Your {top_skill} experience is a genuine strength" if matched_skills else "Thank you for the time you invested"
        body = (
            f"Hi {first_name},\n\n"
            f"Thank you for your interest.{gap_line} {strength_note} — "
            f"we encourage you to apply again once you have strengthened the required areas.\n\n"
            f"Best regards,\nHR Team"
        )
        subject = f"Application Status — {candidate_name}"

    return EmailTemplates(template_type=template_type, subject=subject, body=body)




def build_analytics(results: List[CandidateResult], required_skills: List[str]) -> AnalysisAnalytics:
    if not results:
        return AnalysisAnalytics(
            resumes_uploaded=0,
            average_fit_score=0.0,
            candidates_above_80=0,
            score_distribution={"top": 0, "middle": 0, "low": 0},
            skill_coverage=[],
            candidate_scores=[],
            category_distribution={},
        )

    fit_scores = [candidate.fit_score for candidate in results]
    distribution = {
        "top": len([score for score in fit_scores if score >= 80]),
        "middle": len([score for score in fit_scores if 60 <= score < 80]),
        "low": len([score for score in fit_scores if score < 60]),
    }

    skill_coverage: List[SkillCoverage] = []
    for skill in required_skills:
        matched_count = len([result for result in results if skill in result.matched_skills])
        skill_coverage.append(SkillCoverage(skill=skill, matched_count=matched_count))

    candidate_scores = [
        CandidateScore(
            candidate_name=result.candidate_name,
            fit_score=result.fit_score,
            tier=result.tier,
        )
        for result in results
    ]

    category_distribution: Dict[str, int] = {}
    for r in results:
        if getattr(r, "predicted_category", None):
            key = str(r.predicted_category)
            category_distribution[key] = category_distribution.get(key, 0) + 1

    return AnalysisAnalytics(
        resumes_uploaded=len(results),
        average_fit_score=round(sum(fit_scores) / len(fit_scores), 2),
        candidates_above_80=distribution["top"],
        score_distribution=distribution,
        skill_coverage=skill_coverage,
        candidate_scores=candidate_scores,
        category_distribution=category_distribution,
    )


def extract_jd_target_role(jd_text: str) -> str:
    """
    Lightweight heuristic for a JD target role label.
    Used only for the classifier alignment signal (small weight).
    """
    text = (jd_text or "").lower()
    role_patterns = [
        ("backend developer", [r"backend developer", r"backend engineer", r"python developer", r"api developer"]),
        ("frontend developer", [r"frontend developer", r"front[-\\s]?end developer", r"react developer", r"ui engineer"]),
        ("full stack developer", [r"full stack", r"fullstack"]),
        ("data scientist", [r"data scientist", r"machine learning engineer", r"ml engineer"]),
        ("devops engineer", [r"devops", r"site reliability", r"sre"]),
        ("mobile developer", [r"android developer", r"ios developer", r"mobile developer"]),
    ]
    for label, pats in role_patterns:
        for pat in pats:
            if re.search(pat, text):
                return label.title()
    # Fallback: first 6 words after "hiring" / "looking for"
    m = re.search(r"(hiring|looking for|seeking)\\s+([a-zA-Z\\s]{3,80})", jd_text or "", re.IGNORECASE)
    if m:
        words = re.sub(r"\\s+", " ", m.group(2)).strip().split(" ")[:6]
        return " ".join(words).strip().title()
    return ""


@api_router.get("/")
async def root() -> Dict[str, str]:
    return {"message": "AI Resume Screener API is running"}


@api_router.post("/auth/recruiters/signup", response_model=RecruiterAuthResponse)
async def recruiter_signup(payload: RecruiterSignupRequest):
    if payload.password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="Password and confirm password must match")

    email = payload.email.lower()
    existing = await db.recruiters.find_one({"email": email}, {"_id": 0})
    if existing:
        raise HTTPException(status_code=409, detail="Recruiter email already registered")

    now_iso = datetime.now(timezone.utc).isoformat()
    recruiter_doc = {
        "recruiter_id": str(uuid.uuid4()),
        "name": payload.name.strip(),
        "email": email,
        "company": payload.company.strip(),
        "role": payload.role.strip(),
        "password_hash": get_password_hash(payload.password),
        "created_at": now_iso,
    }

    await db.recruiters.insert_one(dict(recruiter_doc))

    token = create_access_token(email, remember_me=True)
    recruiter_profile = serialize_recruiter_profile(recruiter_doc)
    return RecruiterAuthResponse(access_token=token, recruiter=recruiter_profile)


@api_router.post("/auth/recruiters/login", response_model=RecruiterAuthResponse)
async def recruiter_login(payload: RecruiterLoginRequest):
    email = payload.email.lower()
    recruiter = await db.recruiters.find_one({"email": email}, {"_id": 0})
    if not recruiter:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not verify_password(payload.password, recruiter["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(email, remember_me=payload.remember_me)
    recruiter_profile = serialize_recruiter_profile(recruiter)
    return RecruiterAuthResponse(access_token=token, recruiter=recruiter_profile)


@api_router.get("/auth/recruiters/me", response_model=RecruiterProfile)
async def recruiter_me(current_recruiter: RecruiterProfile = Depends(get_current_recruiter)):
    return current_recruiter


async def parse_analysis_inputs(
    jd_text: str,
    jd_file: Optional[UploadFile],
    resumes_zip: UploadFile,
) -> Dict[str, Any]:
    if not resumes_zip.filename or not resumes_zip.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Please upload resumes as a ZIP file")

    final_jd_text = normalize_text(jd_text)
    if not final_jd_text and jd_file:
        jd_bytes = await jd_file.read()
        final_jd_text = extract_text_by_extension(jd_file.filename or "", jd_bytes)

    if not final_jd_text:
        raise HTTPException(status_code=400, detail="Job description text or file is required")

    zip_bytes = await resumes_zip.read()
    return {"final_jd_text": final_jd_text, "zip_bytes": zip_bytes}


async def update_job_status(
    batch_id: str,
    status: str,
    progress: int,
    processing_logs: List[str],
    error_message: Optional[str] = None,
    recruiter_id: Optional[str] = None,
):
    set_fields: Dict[str, Any] = {
        "batch_id": batch_id,
        "status": status,
        "progress": progress,
        "processing_logs": processing_logs,
        "error_message": error_message,
        "completed": status == "completed",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if recruiter_id:
        set_fields["recruiter_id"] = recruiter_id
    await db.screener_jobs.update_one(
        {"batch_id": batch_id},
        {
            "$set": set_fields,
            "$setOnInsert": {"created_at": datetime.now(timezone.utc).isoformat()},
        },
        upsert=True,
    )


async def perform_analysis(
    final_jd_text: str,
    zip_bytes: bytes,
    forced_batch_id: Optional[str] = None,
) -> AnalysisResponse:
    try:
        resume_documents = extract_resumes_from_zip(zip_bytes)
    except zipfile.BadZipFile:
        raise HTTPException(
            status_code=400,
            detail="Invalid ZIP file content. Please upload a valid ZIP archive.",
        )

    if not resume_documents:
        raise HTTPException(
            status_code=400,
            detail="No readable resumes found in ZIP. Supported formats: PDF, DOCX, TXT",
        )

    processing_logs = [
        "ZIP received successfully",
        f"Extracted {len(resume_documents)} resume(s)",
        "Job description parsed",
        "Running TF-IDF similarity and weighted scoring",
        "Running Smart Portfolio Verifier for GitHub and LinkedIn",
        "Applying ML resume role classifier (supporting signal)",
        "Preparing ranking dashboard payload",
    ]

    # Load classifier once per server session (safe if missing)
    load_resume_classifier()
    jd_target_role = extract_jd_target_role(final_jd_text)

    required_skills = extract_required_skills(final_jd_text)
    nice_to_have_skills = extract_nice_to_have_skills(final_jd_text)
    jd_keywords = extract_top_keywords(final_jd_text)
    jd_years = extract_experience_years(final_jd_text)
    jd_education_level = extract_education_level(final_jd_text)

    # Groq LLM fallback for fuzzy JD tech stack (used when rule-based finds < 2 skills)
    llm_tech_stack_enhanced = False
    if len(required_skills) < 2 and _groq_client:
        llm_skills = hybrid_extractor.groq_tech_stack_extraction(final_jd_text)
        if llm_skills:
            merged = list(dict.fromkeys([*required_skills, *llm_skills]))
            required_skills = merged
            llm_tech_stack_enhanced = True
            processing_logs.append(f"LLM tech stack enhanced: {len(llm_skills)} skills via Groq Llama 3.1 70B")

    resume_texts = [resume["text"] for resume in resume_documents]
    vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), max_features=2500)
    matrix = vectorizer.fit_transform([final_jd_text] + resume_texts)
    similarity_scores = cosine_similarity(matrix[0:1], matrix[1:]).flatten()

    # ── Semaphore: max 4 resumes processed simultaneously (avoids GitHub rate-limit pile-up) ──
    _sem = asyncio.Semaphore(4)

    async def _scan_one_link(url: str) -> LinkScanResult:
        link_type = classify_link_type(url)
        if link_type == "linkedin":
            check = await asyncio.to_thread(verify_linkedin, url)
            return LinkScanResult(
                url=url, link_type=link_type,
                reachable=check["reachable"], valid_format=check["valid"],
                status_code=check.get("status_code"), notes=check.get("notes", ""),
            )
        elif link_type == "portfolio":
            check = await asyncio.to_thread(verify_portfolio, url)
            return LinkScanResult(
                url=url, link_type=link_type,
                reachable=check["reachable"], valid_format=True,
                status_code=check.get("status_code"), notes=check.get("notes", ""),
            )
        else:
            check = await asyncio.to_thread(verify_generic_link, url)
            return LinkScanResult(
                url=url, link_type=link_type,
                reachable=check["reachable"],
                valid_format=bool(extract_github_username(url)) if link_type == "github" else True,
                status_code=check.get("status_code"), notes=check.get("notes", ""),
            )

    async def _process_one_resume(index: int, resume: dict) -> CandidateResult:
        async with _sem:
            resume_text_lower = resume["text"].lower()

            # ── ML resume category prediction (supporting signal) ─────────────
            predicted_category = None
            category_confidence = 0.0
            category_alignment_score = 0
            category_alignment_label = ""
            try:
                pred = predict_resume_category(resume["text"])
                if pred:
                    predicted_category = pred.get("predicted_category")
                    category_confidence = float(pred.get("confidence") or 0.0)
                    alignment = compute_category_alignment(
                        predicted_category=predicted_category,
                        jd_target_role=jd_target_role,
                        confidence=category_confidence,
                    )
                    category_alignment_score = int(alignment.get("alignment_score") or 0)
                    category_alignment_label = str(alignment.get("alignment_label") or "")
            except Exception as exc:
                logger.warning("Category prediction failed (non-fatal): %s", exc)

            matched_skills = [s for s in required_skills if s in resume_text_lower]
            missing_skills  = [s for s in required_skills if s not in resume_text_lower]

            skills_match_score = (
                round((len(matched_skills) / len(required_skills)) * 100, 2)
                if required_skills
                else round(min(100.0, similarity_scores[index] * 100), 2)
            )

            candidate_years = extract_experience_years(resume["text"])
            candidate_education_level = extract_education_level(resume["text"])
            experience_match_score = calculate_experience_score(candidate_years, jd_years)
            education_match_score  = calculate_education_score(candidate_education_level, jd_education_level)

            links = unique_links([
                *resume.get("extracted_links", []),
                *extract_urls(resume["text"]),
                *infer_social_urls_from_text(resume["text"]),
            ])
            github_urls   = [l for l in links if classify_link_type(l) == "github"]
            linkedin_urls = [l for l in links if classify_link_type(l) == "linkedin"]
            portfolio_urls = [l for l in links if classify_link_type(l) == "portfolio"]

            github_url   = github_urls[0]   if github_urls   else None
            linkedin_url = linkedin_urls[0]  if linkedin_urls  else None
            portfolio_url = portfolio_urls[0] if portfolio_urls else None

            # ── Parallel: GitHub API + LinkedIn scraper + all link scans ──────
            gh_task  = asyncio.to_thread(verify_github_portfolio,  github_url,  required_skills, nice_to_have_skills)
            li_task  = asyncio.to_thread(verify_linkedin_portfolio, linkedin_url, required_skills, nice_to_have_skills)
            lnk_tasks = [_scan_one_link(u) for u in links]

            github_verification, linkedin_analysis, *scanned_links_raw = await asyncio.gather(
                gh_task, li_task, *lnk_tasks, return_exceptions=True
            )

            # Unpack GitHub
            if isinstance(github_verification, Exception):
                github_verification = {
                    "github_activity": GitHubActivity(notes="GitHub verification error"),
                    "github_analysis": GitHubPortfolioAnalysis(notes="GitHub verification error"),
                }
            github_activity = github_verification["github_activity"]
            github_analysis = github_verification["github_analysis"]

            # Unpack LinkedIn
            if isinstance(linkedin_analysis, Exception):
                linkedin_analysis = LinkedInPortfolioAnalysis(notes="LinkedIn verification error")

            smart_portfolio = build_smart_portfolio_summary(github_analysis, linkedin_analysis)

            # Unpack link scans
            scanned_links: List[LinkScanResult] = []
            linkedin_valid = linkedin_reachable = portfolio_reachable = False
            for res in scanned_links_raw:
                if isinstance(res, LinkScanResult):
                    scanned_links.append(res)
                    if res.link_type == "linkedin":
                        linkedin_valid     = linkedin_valid or res.valid_format
                        linkedin_reachable = linkedin_reachable or res.reachable
                    elif res.link_type == "portfolio":
                        portfolio_reachable = portfolio_reachable or res.reachable

            activity_bonus = smart_portfolio.verification_bonus
            if linkedin_valid and linkedin_reachable: activity_bonus += 2
            if portfolio_reachable:                   activity_bonus += 2
            activity_bonus = round(min(65.0, activity_bonus), 2)

            similarity_score = round(float(similarity_scores[index] * 100), 2)

            # Category alignment: small bounded bonus (max ~6 points)
            category_boost = 0.0
            if category_alignment_score > 0 and category_confidence > 0:
                category_boost = round((category_alignment_score / 10.0) * 6.0, 2)

            fit_score = round(
                min(100.0,
                    0.45 * similarity_score
                    + 0.30 * skills_match_score
                    + 0.15 * experience_match_score
                    + 0.10 * education_match_score
                    + activity_bonus
                    + category_boost),
                2,
            )

            suggestions: List[str] = []
            if missing_skills:
                suggestions.append(f"Missing key skills: {', '.join(missing_skills[:6])}")
            if candidate_years < jd_years and jd_years > 0:
                suggestions.append(f"Experience below requirement ({candidate_years}y vs {jd_years}y target)")
            if not github_url:
                suggestions.append("Add an active GitHub profile for stronger technical validation")
            if not smart_portfolio.stack_experience_verified:
                suggestions.append("Portfolio evidence is weak for JD stack. Add clearer project links and recent contributions.")

            resume_file_bytes = resume.get("file_bytes")
            resume_filename   = resume.get("source_file", "")

            ats = await asyncio.to_thread(compute_ats_score, resume["text"], resume_file_bytes, resume_filename)

            achievements = analyze_notable_achievements(resume["text"], github_analysis, linkedin_analysis)
            verification = calculate_verification_summary(
                resume["text"],
                github_analysis,
                linkedin_analysis,
                scanned_links,
                portfolio_reachable,
            )
            shortlist_recommendation = determine_shortlist_recommendation(
                fit_score,
                missing_skills,
                verification,
            )
            evidence_summary = build_evidence_summary(
                matched_skills,
                missing_skills,
                github_analysis,
                linkedin_analysis,
            )
            email_tpl    = generate_email_template(resume["candidate_name"], fit_score, matched_skills, missing_skills, github_activity.username)
            scanned_links_summary = ScannedLinksSummary(
                total_links=len(scanned_links),
                reachable_links=len([link for link in scanned_links if link.reachable]),
                github_found=bool(github_url),
                linkedin_found=bool(linkedin_url),
                portfolio_reachable=portfolio_reachable,
            )
            github_analysis_summary = GitHubAnalysisSummary(
                username=str(github_analysis.username or ""),
                top_project=str(github_analysis.top_projects[0].repo_name) if github_analysis.top_projects else "",
                stack_coverage_pct=float(github_analysis.stack_coverage_pct or 0.0),
                activity_status=str(github_analysis.activity_status or "Unknown"),
            )
            linkedin_summary = LinkedInSummary(
                verified=linkedin_analysis.verified,
                headline=linkedin_analysis.headline or "",
                notes=linkedin_analysis.notes or "",
            )

            return CandidateResult(
                candidate_id=str(uuid.uuid4()),
                candidate_name=resume["candidate_name"],
                candidate_email=resume.get("candidate_email"),
                source_file=resume["source_file"],
                fit_score=fit_score,
                tier=classify_tier(fit_score),
                similarity_score=similarity_score,
                skills_match_score=round(skills_match_score, 2),
                experience_match_score=round(experience_match_score, 2),
                education_match_score=round(education_match_score, 2),
                matched_skills=matched_skills,
                missing_skills=missing_skills,
                suggested_improvements=suggestions,
                extracted_years_experience=candidate_years,
                github_extraction_method=resume.get("github_extraction_method", "rule-based"),
                ats_score=ats,
                verification_summary=verification,
                evidence_summary=evidence_summary,
                shortlist_recommendation=shortlist_recommendation,
                github_analysis_summary=github_analysis_summary,
                linkedin_summary=linkedin_summary,
                scanned_links_summary=scanned_links_summary,
                notable_achievements=achievements,
                email_template=email_tpl,
                verified_links=VerifiedLinks(
                    github_url=github_url,
                    linkedin_url=linkedin_url,
                    portfolio_url=portfolio_url,
                    github_urls=github_urls,
                    linkedin_urls=linkedin_urls,
                    portfolio_urls=portfolio_urls,
                    scanned_links=scanned_links,
                    github=github_activity,
                    github_analysis=github_analysis,
                    linkedin_analysis=linkedin_analysis,
                    smart_portfolio=smart_portfolio,
                    linkedin_valid=linkedin_valid,
                    linkedin_reachable=linkedin_reachable,
                    portfolio_reachable=portfolio_reachable,
                    activity_bonus=round(activity_bonus, 2),
                ),
                predicted_category=predicted_category,
                category_confidence=round(float(category_confidence), 4),
                category_alignment_score=category_alignment_score,
                category_alignment_label=category_alignment_label,
            )

    # ── Run all resumes in parallel ────────────────────────────────────────────
    results_raw = await asyncio.gather(
        *[_process_one_resume(i, r) for i, r in enumerate(resume_documents)],
        return_exceptions=True,
    )
    for idx, raw in enumerate(results_raw):
        if isinstance(raw, Exception):
            logger.warning("Candidate processing failed at index %s: %s", idx, raw)
    results: List[CandidateResult] = [r for r in results_raw if isinstance(r, CandidateResult)]

    results.sort(key=lambda item: item.fit_score, reverse=True)
    analytics = build_analytics(results, required_skills)
    generated_at = datetime.now(timezone.utc).isoformat()
    batch_id = forced_batch_id or str(uuid.uuid4())

    return AnalysisResponse(
        batch_id=batch_id,
        generated_at=generated_at,
        jd_keywords=jd_keywords,
        required_skills=required_skills,
        nice_to_have_skills=nice_to_have_skills,
        processing_logs=processing_logs,
        results=results,
        analytics=analytics,
        llm_tech_stack_enhanced=llm_tech_stack_enhanced,
    )


async def process_analysis_background(
    batch_id: str, final_jd_text: str, zip_bytes: bytes, recruiter_id: str = ""
):
    try:
        await update_job_status(
            batch_id=batch_id,
            status="processing",
            progress=20,
            processing_logs=["Batch queued", "Analyzing resumes in background"],
            recruiter_id=recruiter_id or None,
        )
        payload = await perform_analysis(final_jd_text, zip_bytes, forced_batch_id=batch_id)
        doc = payload.model_dump()
        if recruiter_id:
            doc["recruiter_id"] = recruiter_id
        await db.screener_batches.insert_one(doc)
        await update_job_status(
            batch_id=batch_id,
            status="completed",
            progress=100,
            processing_logs=[*payload.processing_logs, "Batch completed successfully"],
            recruiter_id=recruiter_id or None,
        )
    except HTTPException as exc:
        await update_job_status(
            batch_id=batch_id,
            status="failed",
            progress=100,
            processing_logs=["Processing failed"],
            error_message=str(exc.detail),
            recruiter_id=recruiter_id or None,
        )
    except Exception as exc:
        await update_job_status(
            batch_id=batch_id,
            status="failed",
            progress=100,
            processing_logs=["Processing failed unexpectedly"],
            error_message=str(exc),
            recruiter_id=recruiter_id or None,
        )


@api_router.post("/screener/analyze/start", response_model=ScreenerStatusResponse)
async def start_analysis(
    jd_text: str = Form(default=""),
    jd_file: Optional[UploadFile] = File(default=None),
    resumes_zip: UploadFile = File(...),
    current_recruiter: RecruiterProfile = Depends(get_current_recruiter),
):
    parsed = await parse_analysis_inputs(jd_text, jd_file, resumes_zip)
    batch_id = str(uuid.uuid4())
    recruiter_id = current_recruiter.recruiter_id

    await update_job_status(
        batch_id=batch_id,
        status="processing",
        progress=10,
        processing_logs=["Files received", "Queued for processing"],
        recruiter_id=recruiter_id,
    )

    asyncio.create_task(
        process_analysis_background(
            batch_id=batch_id,
            final_jd_text=parsed["final_jd_text"],
            zip_bytes=parsed["zip_bytes"],
            recruiter_id=recruiter_id,
        )
    )

    return ScreenerStatusResponse(
        batch_id=batch_id,
        status="processing",
        progress=10,
        processing_logs=["Files received", "Queued for processing"],
        completed=False,
    )


@api_router.get("/screener/status/{batch_id}", response_model=ScreenerStatusResponse)
async def get_screener_status(
    batch_id: str,
    current_recruiter: RecruiterProfile = Depends(get_current_recruiter),
):
    recruiter_id = current_recruiter.recruiter_id
    job = await db.screener_jobs.find_one({"batch_id": batch_id}, {"_id": 0})
    if job:
        if job.get("recruiter_id") and job["recruiter_id"] != recruiter_id:
            raise HTTPException(status_code=403, detail="Access denied")
        return ScreenerStatusResponse(**job)

    completed = await db.screener_batches.find_one({"batch_id": batch_id}, {"_id": 0, "batch_id": 1, "recruiter_id": 1})
    if completed:
        if completed.get("recruiter_id") and completed["recruiter_id"] != recruiter_id:
            raise HTTPException(status_code=403, detail="Access denied")
        return ScreenerStatusResponse(
            batch_id=batch_id,
            status="completed",
            progress=100,
            processing_logs=["Batch completed successfully"],
            completed=True,
        )

    raise HTTPException(status_code=404, detail="Batch not found")


@api_router.post("/screener/analyze", response_model=AnalysisResponse)
async def analyze_resumes(
    jd_text: str = Form(default=""),
    jd_file: Optional[UploadFile] = File(default=None),
    resumes_zip: UploadFile = File(...),
    current_recruiter: RecruiterProfile = Depends(get_current_recruiter),
):
    parsed = await parse_analysis_inputs(jd_text, jd_file, resumes_zip)
    payload = await perform_analysis(parsed["final_jd_text"], parsed["zip_bytes"])
    doc = payload.model_dump()
    doc["recruiter_id"] = current_recruiter.recruiter_id
    await db.screener_batches.insert_one(doc)
    return payload


def _check_batch_ownership(doc: Dict[str, Any], recruiter_id: str) -> None:
    """Raise 403 if the batch belongs to a different recruiter."""
    stored = doc.get("recruiter_id")
    if stored and stored != recruiter_id:
        raise HTTPException(status_code=403, detail="Access denied: this batch belongs to another account")


@api_router.get("/screener/results/{batch_id}", response_model=AnalysisResponse)
async def get_screener_results(
    batch_id: str,
    current_recruiter: RecruiterProfile = Depends(get_current_recruiter),
):
    doc = await db.screener_batches.find_one({"batch_id": batch_id}, {"_id": 0})
    if not doc:
        job = await db.screener_jobs.find_one({"batch_id": batch_id}, {"_id": 0, "status": 1, "recruiter_id": 1})
        if job:
            _check_batch_ownership(job, current_recruiter.recruiter_id)
            if job.get("status") in {"processing", "queued"}:
                raise HTTPException(status_code=409, detail="Batch is still processing")
            if job.get("status") == "failed":
                raise HTTPException(status_code=500, detail="Batch processing failed")
        raise HTTPException(status_code=404, detail="Analysis batch not found")
    _check_batch_ownership(doc, current_recruiter.recruiter_id)
    return AnalysisResponse(**doc)


@api_router.get("/screener/analytics/{batch_id}", response_model=AnalysisAnalytics)
async def get_screener_analytics(
    batch_id: str,
    current_recruiter: RecruiterProfile = Depends(get_current_recruiter),
):
    doc = await db.screener_batches.find_one(
        {"batch_id": batch_id}, {"_id": 0, "analytics": 1, "recruiter_id": 1}
    )
    if not doc:
        job = await db.screener_jobs.find_one({"batch_id": batch_id}, {"_id": 0, "status": 1})
        if job and job.get("status") in {"processing", "queued"}:
            raise HTTPException(status_code=409, detail="Batch is still processing")
        raise HTTPException(status_code=404, detail="Analytics not found")
    _check_batch_ownership(doc, current_recruiter.recruiter_id)
    return AnalysisAnalytics(**doc["analytics"])


@api_router.get("/screener/batches")
async def list_screening_batches(
    current_recruiter: RecruiterProfile = Depends(get_current_recruiter),
):
    """Return a summary list of all batches belonging to the authenticated recruiter."""
    cursor = db.screener_batches.find(
        {"recruiter_id": current_recruiter.recruiter_id}
    ).sort("generated_at", -1).limit(30)
    raw = await cursor.to_list(length=30)
    summaries = []
    for b in raw:
        results = b.get("results", [])
        top_candidates = [
            {
                "name": c.get("candidate_name", ""),
                "fit_score": c.get("fit_score", 0),
                "tier": c.get("tier", ""),
                "github_username": (c.get("verified_links") or {}).get("github", {}).get("username") or "",
            }
            for c in results[:3]
        ]
        analytics = b.get("analytics", {})
        summaries.append({
            "batch_id": b.get("batch_id", ""),
            "generated_at": b.get("generated_at", ""),
            "required_skills": b.get("required_skills", [])[:8],
            "resumes_uploaded": analytics.get("resumes_uploaded", len(results)),
            "average_fit_score": analytics.get("average_fit_score", 0),
            "candidates_above_80": analytics.get("candidates_above_80", 0),
            "score_distribution": analytics.get("score_distribution", {}),
            "llm_tech_stack_enhanced": b.get("llm_tech_stack_enhanced", False),
            "top_candidates": top_candidates,
        })
    return summaries


@api_router.get("/screener/heatmap/{batch_id}")
async def get_skill_heatmap(
    batch_id: str,
    current_recruiter: RecruiterProfile = Depends(get_current_recruiter),
):
    """Return skill × candidate matrix for the heatmap visualisation."""
    doc = await db.screener_batches.find_one({"batch_id": batch_id}, {"_id": 0, "results": 1, "required_skills": 1, "recruiter_id": 1})
    if not doc:
        raise HTTPException(status_code=404, detail="Batch not found")
    _check_batch_ownership(doc, current_recruiter.recruiter_id)

    skills = doc.get("required_skills", [])[:12]
    rows = []
    for candidate in doc.get("results", [])[:20]:
        matched = set(candidate.get("matched_skills", []))
        row = {
            "candidate": candidate.get("candidate_name", ""),
            "fit_score": candidate.get("fit_score", 0),
        }
        for skill in skills:
            row[skill] = skill in matched
        rows.append(row)

    return {"skills": skills, "candidates": rows}


@api_router.get("/screener/llm-stats")
async def get_llm_stats():
    """Return real-time LLM usage statistics for the current server session."""
    return {
        "groq_enabled": _groq_client is not None,
        "model": "llama-3.3-70b-versatile",
        "github_fallback_calls": LLM_STATS["github_fallback_calls"],
        "tech_stack_calls": LLM_STATS["tech_stack_calls"],
        "total_llm_calls": LLM_STATS["total_calls"],
        "note": "Groq is used only when rule-based extraction fails (5% edge cases). Free tier: 1M tokens/day.",
    }


@api_router.get("/screener/export/{batch_id}")
async def export_results_csv(
    batch_id: str,
    current_recruiter: RecruiterProfile = Depends(get_current_recruiter),
):
    doc = await db.screener_batches.find_one(
        {"batch_id": batch_id}, {"_id": 0, "results": 1, "recruiter_id": 1}
    )
    if not doc:
        job = await db.screener_jobs.find_one({"batch_id": batch_id}, {"_id": 0, "status": 1})
        if job and job.get("status") in {"processing", "queued"}:
            raise HTTPException(status_code=409, detail="Batch is still processing")
        raise HTTPException(status_code=404, detail="Batch not found")
    _check_batch_ownership(doc, current_recruiter.recruiter_id)

    rows: List[Dict[str, Any]] = []
    for candidate in doc.get("results", []):
        rows.append(
            {
                "candidate_name": candidate.get("candidate_name"),
                "source_file": candidate.get("source_file"),
                "fit_score": candidate.get("fit_score"),
                "tier": candidate.get("tier"),
                "shortlist_recommendation": candidate.get("shortlist_recommendation", "Review"),
                "similarity_score": candidate.get("similarity_score"),
                "skills_match_score": candidate.get("skills_match_score"),
                "experience_match_score": candidate.get("experience_match_score"),
                "education_match_score": candidate.get("education_match_score"),
                "evidence_summary": candidate.get("evidence_summary", ""),
                "matched_skills": ", ".join(candidate.get("matched_skills", [])),
                "missing_skills": ", ".join(candidate.get("missing_skills", [])),
                "verification_status": candidate.get("verification_summary", {}).get("status", ""),
                "verification_checks_passed": candidate.get("verification_summary", {}).get("checks_passed", 0),
                "github_username": candidate.get("verified_links", {})
                .get("github", {})
                .get("username"),
                "github_repo_count": candidate.get("verified_links", {})
                .get("github", {})
                .get("repo_count"),
                "linkedin_urls": ", ".join(
                    candidate.get("verified_links", {}).get("linkedin_urls", [])
                ),
                "links_scanned_count": len(
                    candidate.get("verified_links", {}).get("scanned_links", [])
                ),
                "smart_verification_bonus": candidate.get("verified_links", {})
                .get("smart_portfolio", {})
                .get("verification_bonus"),
                "stack_coverage_pct": candidate.get("verified_links", {})
                .get("github_analysis", {})
                .get("stack_coverage_pct"),
                "jd_relevant_projects": candidate.get("verified_links", {})
                .get("github_analysis", {})
                .get("jd_relevant_projects"),
                "best_project_complexity": candidate.get("verified_links", {})
                .get("github_analysis", {})
                .get("best_project_complexity"),
                "activity_bonus": candidate.get("verified_links", {}).get("activity_bonus"),
                "predicted_category": candidate.get("predicted_category"),
                "category_confidence": candidate.get("category_confidence", 0.0),
                "category_alignment_score": candidate.get("category_alignment_score", 0),
                "category_alignment_label": candidate.get("category_alignment_label", ""),
            }
        )

    csv_buffer = io.StringIO()
    pd.DataFrame(rows).to_csv(csv_buffer, index=False)
    csv_buffer.seek(0)

    headers = {
        "Content-Disposition": f"attachment; filename=resume_screening_{batch_id}.csv"
    }
    return StreamingResponse(iter([csv_buffer.getvalue()]), media_type="text/csv", headers=headers)


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 9 — EMAIL SENDING (Multi-provider: SMTP → local outbox fallback)
# ─────────────────────────────────────────────────────────────────────────────

SMTP_HOST = os.environ.get("PIXLS_SMTP_HOST", "smtp.gmail.com")
SMTP_USER = os.environ.get("PIXLS_SMTP_USER", "")
SMTP_PASS = os.environ.get("PIXLS_SMTP_PASS", "")
EMAIL_RATE_LIMIT = int(os.environ.get("EMAIL_RATE_LIMIT_PER_HOUR", "50"))

# Local outbox directory — emails saved here when SMTP is unavailable
_OUTBOX_DIR = ROOT_DIR / "email_outbox"
_OUTBOX_DIR.mkdir(exist_ok=True)

_SMTP_PLACEHOLDER_USERS = {"your_gmail@gmail.com", "", "youremail@gmail.com"}
_SMTP_PLACEHOLDER_PASSES = {"your_16char_app_password", "", "yourpassword"}


class SendEmailRequest(BaseModel):
    to: EmailStr
    subject: str = Field(..., min_length=1, max_length=200)
    body: str = Field(..., min_length=1, max_length=5000)
    cc: Optional[EmailStr] = None
    bcc: Optional[EmailStr] = None
    template_type: str = "advance"
    candidate_id: Optional[str] = None
    candidate_name: Optional[str] = None


class SendEmailResponse(BaseModel):
    success: bool
    message: str
    delivery_mode: str = "sent"   # "sent" | "queued"
    email_log_id: Optional[str] = None


def _build_mime_message(
    to: str,
    subject: str,
    body: str,
    sender_name: str,
    cc: Optional[str],
    bcc: Optional[str],
):
    """Build a MIME email message object."""
    import smtplib  # noqa: F401
    from email.header import Header
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.utils import formataddr

    safe_sender = formataddr((str(Header(sender_name, "utf-8")), SMTP_USER or "noreply@pixls.app"))
    msg = MIMEMultipart("alternative")
    msg["From"] = safe_sender
    msg["To"] = to
    msg["Subject"] = Header(subject, "utf-8")
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    msg.attach(MIMEText(body, "plain", "utf-8"))

    html_safe_body = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html_body = (
        "<!DOCTYPE html><html><head>"
        '<meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<style>"
        "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;"
        "background:#f9fafb;margin:0;padding:24px}"
        ".card{background:#fff;border-radius:12px;padding:32px;max-width:600px;"
        "margin:auto;border:1px solid #e5e7eb}"
        "pre{white-space:pre-wrap;font-family:inherit;font-size:15px;line-height:1.7;color:#374151}"
        ".footer{margin-top:24px;padding-top:16px;border-top:1px solid #e5e7eb;"
        "font-size:12px;color:#9ca3af}"
        "</style></head><body>"
        f'<div class="card"><pre>{html_safe_body}</pre>'
        '<div class="footer">Sent via PIXLS Hiring Platform</div></div>'
        "</body></html>"
    )
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return msg


def _smtp_send_sync(
    to: str,
    subject: str,
    body: str,
    sender_name: str,
    cc: Optional[str],
    bcc: Optional[str],
) -> str:
    """
    Try to send via SMTP.  Returns 'sent' on success.
    Falls back to writing to local outbox on any failure; returns 'queued'.
    Never raises — the caller always gets a usable result.
    """
    import smtplib
    import ssl

    smtp_ready = (
        SMTP_USER
        and SMTP_USER not in _SMTP_PLACEHOLDER_USERS
        and SMTP_PASS
        and SMTP_PASS not in _SMTP_PLACEHOLDER_PASSES
    )

    if smtp_ready:
        msg = _build_mime_message(to, subject, body, sender_name, cc, bcc)
        recipients = [to] + ([cc] if cc else []) + ([bcc] if bcc else [])

        # Try STARTTLS first (port 587), then SSL (port 465)
        for attempt_fn in (_try_starttls, _try_ssl):
            try:
                attempt_fn(msg, recipients)
                logger.info("Email delivered via SMTP to %s", to)
                return "sent"
            except smtplib.SMTPAuthenticationError as exc:
                code = exc.smtp_code
                raw = exc.smtp_error
                if isinstance(raw, bytes):
                    raw = raw.decode(errors="replace")
                logger.error("SMTP auth failed (%s %s) — falling back to outbox", code, raw)
                break   # auth failure won't be fixed by retrying on another port
            except Exception as exc:
                logger.warning("SMTP attempt failed (%s: %s), trying next method", type(exc).__name__, exc)

    # ── Local outbox fallback ─────────────────────────────────────────────────
    _save_to_outbox(to, subject, body, sender_name, cc, bcc)
    return "queued"


def _try_starttls(msg, recipients: List[str]) -> None:
    import smtplib
    import ssl
    with smtplib.SMTP(SMTP_HOST, 587, timeout=20) as server:
        server.ehlo()
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, recipients, msg.as_string())


def _try_ssl(msg, recipients: List[str]) -> None:
    import smtplib
    import ssl
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, 465, context=ctx, timeout=20) as server:
        server.ehlo()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, recipients, msg.as_string())


def _save_to_outbox(
    to: str,
    subject: str,
    body: str,
    sender_name: str,
    cc: Optional[str],
    bcc: Optional[str],
) -> None:
    """Persist email to local JSON file so no email is ever lost."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    filename = _OUTBOX_DIR / f"{ts}_{uuid.uuid4().hex[:8]}.json"
    payload = {
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "from": sender_name,
        "to": to,
        "cc": cc,
        "bcc": bcc,
        "subject": subject,
        "body": body,
        "status": "queued",
    }
    filename.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    logger.info("Email queued to outbox: %s", filename.name)


@api_router.post("/email/send", response_model=SendEmailResponse)
async def send_candidate_email(
    payload: SendEmailRequest,
    current_recruiter: RecruiterProfile = Depends(get_current_recruiter),
):
    recruiter_id = current_recruiter.recruiter_id

    # Rate limiting
    one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    recent_count = await db.email_logs.count_documents({
        "recruiter_id": recruiter_id,
        "sent_at": {"$gte": one_hour_ago},
    })
    if recent_count >= EMAIL_RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit reached: max {EMAIL_RATE_LIMIT} emails per hour.",
        )

    sender_name = f"{current_recruiter.name} - {current_recruiter.company}"
    delivery_mode = await asyncio.to_thread(
        _smtp_send_sync,
        payload.to,
        payload.subject,
        payload.body,
        sender_name,
        payload.cc,
        payload.bcc,
    )

    # Persist log regardless of delivery mode
    log_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    await db.email_logs.insert_one({
        "email_log_id": log_id,
        "recruiter_id": recruiter_id,
        "recruiter_name": current_recruiter.name,
        "to": payload.to,
        "cc": payload.cc,
        "bcc": payload.bcc,
        "subject": payload.subject,
        "body": payload.body,
        "template_type": payload.template_type,
        "candidate_id": payload.candidate_id,
        "candidate_name": payload.candidate_name,
        "sent_at": now_iso,
        "delivery_mode": delivery_mode,
    })

    if delivery_mode == "sent":
        msg = f"Email sent to {payload.to}"
    else:
        msg = (
            f"Email queued for {payload.to}. "
            "SMTP is not configured — email saved to outbox. "
            "Set PIXLS_SMTP_USER + PIXLS_SMTP_PASS in backend/.env to enable real delivery."
        )

    return SendEmailResponse(
        success=True,
        message=msg,
        delivery_mode=delivery_mode,
        email_log_id=log_id,
    )


@api_router.get("/email/test-smtp")
async def test_smtp_connection(
    current_recruiter: RecruiterProfile = Depends(get_current_recruiter),
):
    """Diagnose SMTP connectivity — returns a clear status without sending anything."""
    import smtplib
    import ssl

    if not SMTP_USER or SMTP_USER in _SMTP_PLACEHOLDER_USERS:
        return {"status": "unconfigured", "detail": "PIXLS_SMTP_USER not set in backend/.env"}
    if not SMTP_PASS or SMTP_PASS in _SMTP_PLACEHOLDER_PASSES:
        return {"status": "unconfigured", "detail": "PIXLS_SMTP_PASS not set in backend/.env"}

    try:
        with smtplib.SMTP(SMTP_HOST, 587, timeout=10) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASS)
        return {"status": "ok", "detail": f"SMTP authenticated as {SMTP_USER} ✅"}
    except smtplib.SMTPAuthenticationError as exc:
        raw = exc.smtp_error
        if isinstance(raw, bytes):
            raw = raw.decode(errors="replace")
        return {
            "status": "auth_failed",
            "detail": (
                f"Gmail rejected the password ({exc.smtp_code}: {raw}). "
                "Generate a fresh App Password at https://myaccount.google.com/apppasswords "
                "(requires 2-Step Verification to be ON on the account)."
            ),
        }
    except Exception as exc:
        return {"status": "error", "detail": f"{type(exc).__name__}: {exc}"}


@api_router.get("/email/history")
async def get_email_history(
    current_recruiter: RecruiterProfile = Depends(get_current_recruiter),
):
    """Return the last 200 emails sent by this recruiter, newest first."""
    cursor = db.email_logs.find(
        {"recruiter_id": current_recruiter.recruiter_id},
        {"_id": 0},
    ).sort("sent_at", -1).limit(200)
    logs = [doc async for doc in cursor]
    return {"logs": logs, "total": len(logs)}


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()