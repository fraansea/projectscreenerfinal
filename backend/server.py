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
from typing import Any, Dict, List, Optional

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


class BiasFlags(BaseModel):
    gender_skew_detected: bool = False
    university_bias_detected: bool = False
    flags: List[str] = Field(default_factory=list)
    diversity_note: str = ""


class CareerTrajectory(BaseModel):
    score: int = 0
    label: str = "Stable"
    notes: List[str] = Field(default_factory=list)


class TrustScore(BaseModel):
    score: int = 100
    label: str = "High"
    badge: str = "🟢"
    flags: List[str] = Field(default_factory=list)


class InterviewQuestions(BaseModel):
    questions: List[str] = Field(default_factory=list)
    generated_by: str = "rule-based"


class EmailTemplates(BaseModel):
    template_type: str = "advance"
    subject: str = ""
    body: str = ""


class ResumeAdvice(BaseModel):
    advice: List[str] = Field(default_factory=list)
    priority_fix: str = ""


class CandidateResult(BaseModel):
    candidate_id: str
    candidate_name: str
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
    bias_flags: BiasFlags = Field(default_factory=BiasFlags)
    career_trajectory: CareerTrajectory = Field(default_factory=CareerTrajectory)
    trust_score: TrustScore = Field(default_factory=TrustScore)
    interview_questions: InterviewQuestions = Field(default_factory=InterviewQuestions)
    email_template: EmailTemplates = Field(default_factory=EmailTemplates)
    resume_advice: ResumeAdvice = Field(default_factory=ResumeAdvice)


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
    payload = {"sub": subject_email.lower(), "exp": expire}
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


def safe_url_scan(url: str) -> Dict[str, Any]:
    headers = {"User-Agent": "resume-screener-app/1.0"}
    try:
        response = requests.get(url, timeout=2, allow_redirects=True, headers=headers)
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


def verify_linkedin_portfolio(
    url: Optional[str], jd_required_skills: List[str], jd_nice_to_have: List[str]
) -> LinkedInPortfolioAnalysis:
    if not url:
        return LinkedInPortfolioAnalysis(notes="No LinkedIn profile detected")

    if not re.search(r"linkedin\.com/(in|pub|company)/", url):
        return LinkedInPortfolioAnalysis(profile_url=url, notes="Invalid LinkedIn URL format")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "en-US,en;q=0.8",
    }

    try:
        response = requests.get(url, headers=headers, timeout=4, allow_redirects=True)
    except Exception:
        return LinkedInPortfolioAnalysis(profile_url=url, notes="Unable to reach LinkedIn profile")

    if response.status_code >= 400 and response.status_code not in {401, 403, 429, 999}:
        return LinkedInPortfolioAnalysis(profile_url=url, notes="LinkedIn profile unavailable")

    soup = BeautifulSoup(response.text, "html.parser")
    page_title = normalize_text(soup.title.text) if soup.title and soup.title.text else ""
    meta_desc = soup.find("meta", attrs={"name": "description"})
    description = normalize_text(meta_desc.get("content", "")) if meta_desc else ""
    text_blob = normalize_text(soup.get_text(" ", strip=True)).lower()

    jd_terms = sorted(list(set([*jd_required_skills, *jd_nice_to_have])))
    jd_keywords_found = [term for term in jd_terms if term.lower() in text_blob]
    projects_found = len(re.findall(r"\bproject(s)?\b", text_blob))
    connections_count = extract_linkedin_connections(text_blob)
    total_experience_years = extract_experience_years(text_blob)
    premium_detected = "premium" in text_blob

    # Extract achievements from LinkedIn public page text
    _ACHIEVEMENT_MARKERS = [
        "award", "achievement", "honor", "prize", "winner", "recognition",
        "best paper", "hackathon", "rank 1", "first place", "gold medal",
        "published", "speaker", "keynote", "patent", "scholarship", "fellowship",
        "dean's list", "cum laude", "distinction", "merit",
    ]
    achievements: List[str] = []
    for line in response.text.splitlines():
        line_clean = normalize_text(line)
        line_lower = line_clean.lower()
        if any(marker in line_lower for marker in _ACHIEVEMENT_MARKERS):
            if 15 < len(line_clean) < 180:
                achievements.append(line_clean)
    achievements = list(dict.fromkeys(achievements))[:8]

    # Extract certification names
    _CERT_MARKERS = [
        "certified", "certification", "certificate", "aws certified",
        "google certified", "microsoft certified", "pmp", "cpa", "cfa",
        "comptia", "cisco", "oracle certified", "scrum master", "coursera",
        "udemy", "linkedin learning", "pluralsight",
    ]
    certifications: List[str] = []
    for line in response.text.splitlines():
        line_clean = normalize_text(line)
        line_lower = line_clean.lower()
        if any(marker in line_lower for marker in _CERT_MARKERS):
            if 10 < len(line_clean) < 150:
                certifications.append(line_clean)
    certifications = list(dict.fromkeys(certifications))[:6]

    # Extract project title hints from meta / h1-h3 tags
    project_titles: List[str] = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
        text_val = normalize_text(tag.get_text())
        if 8 < len(text_val) < 100 and "project" in text_val.lower():
            project_titles.append(text_val)
    project_titles = list(dict.fromkeys(project_titles))[:5]

    linkedin_score = 0.0
    if projects_found > 0:
        linkedin_score += 8
    if jd_keywords_found:
        linkedin_score += 10
    if connections_count >= 500:
        linkedin_score += 5
    if premium_detected:
        linkedin_score += 3
    if achievements:
        linkedin_score += 7
    if certifications:
        linkedin_score += 5

    return LinkedInPortfolioAnalysis(
        verified=True,
        profile_url=url,
        headline=description or page_title,
        current_title=page_title,
        total_experience_years=total_experience_years,
        projects_found=projects_found,
        jd_keywords_found=jd_keywords_found,
        connections_count=connections_count,
        premium_detected=premium_detected,
        verification_score=round(linkedin_score, 2),
        notes="LinkedIn profile analyzed from public content",
        achievements=achievements,
        certifications=certifications,
        project_titles=project_titles,
    )


def github_api_get_json(endpoint: str) -> Dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "resume-screener-app/1.0",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    try:
        response = requests.get(
            f"https://api.github.com{endpoint}",
            headers=headers,
            timeout=3,
        )
    except Exception:
        return {"ok": False, "status_code": None, "data": None}

    if response.status_code >= 400:
        return {"ok": False, "status_code": response.status_code, "data": None}

    try:
        return {
            "ok": True,
            "status_code": response.status_code,
            "data": response.json(),
            "headers": response.headers,
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
        return {
            "github_activity": GitHubActivity(
                username=username,
                notes="GitHub username not found or API limit reached",
            ),
            "github_analysis": GitHubPortfolioAnalysis(
                profile_url=github_url,
                username=username,
                notes="Unable to fetch GitHub profile data",
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
                    candidate_name = Path(file_name).stem.replace("_", " ").replace("-", " ")
                    resumes.append(
                        {
                            "candidate_name": candidate_name.title(),
                            "source_file": Path(file_name).name,
                            "text": merged_text,
                            "extracted_links": extracted_links,
                            "github_extraction_method": github_extraction_method,
                            "file_bytes": raw_bytes,
                        }
                    )
    return resumes


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


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 2 — BIAS & FAIRNESS MONITOR
# ─────────────────────────────────────────────────────────────────────────────

_PRESTIGE_BIAS_UNIVERSITIES = [
    "iit", "iim", "mit", "stanford", "harvard", "oxford", "cambridge",
    "nit", "bits pilani", "vit",
]
_GENDER_FEMALE_NAMES = {
    "alice", "mary", "emma", "sophia", "olivia", "ava", "isabella", "mia",
    "anjali", "priya", "divya", "sneha", "pooja", "ananya", "lakshmi",
    "fatima", "sara", "aisha", "nadia", "maria",
}
_GENDER_MALE_NAMES = {
    "james", "john", "robert", "michael", "william", "david", "joseph",
    "rahul", "amit", "arun", "arjun", "vikram", "rajesh", "suresh",
    "mohammed", "ali", "omar", "carlos", "diego",
}


def compute_bias_flags(candidate_name: str, text: str) -> BiasFlags:
    flags: List[str] = []
    text_lower = text.lower()
    first_name = candidate_name.split()[0].lower() if candidate_name else ""

    gender_skew = False
    if first_name in _GENDER_FEMALE_NAMES:
        flags.append("Name may trigger unconscious gender bias — blind review recommended")
        gender_skew = True
    elif first_name in _GENDER_MALE_NAMES:
        flags.append("Name detected — consider anonymising for first-pass screening")

    uni_bias = False
    for uni in _PRESTIGE_BIAS_UNIVERSITIES:
        if uni in text_lower:
            flags.append(f"Prestige-university bias risk ({uni.upper()} detected) — evaluate skills independently")
            uni_bias = True
            break

    diversity_note = ""
    if gender_skew or uni_bias:
        diversity_note = "Bias signals detected. Consider anonymised shortlisting for fairness."
    else:
        diversity_note = "No strong bias signals detected in this candidate profile."

    return BiasFlags(
        gender_skew_detected=gender_skew,
        university_bias_detected=uni_bias,
        flags=flags,
        diversity_note=diversity_note,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 4 — CAREER TRAJECTORY SCORING
# ─────────────────────────────────────────────────────────────────────────────

_ROLE_LEVELS: Dict[str, int] = {
    "intern": 1, "trainee": 1, "apprentice": 1,
    "junior": 2, "associate": 3,
    "mid-level": 4, "mid level": 4,
    "senior": 5, "lead": 6, "principal": 7, "staff": 7,
    "manager": 8, "director": 9, "vp": 10, "cto": 10, "ceo": 10,
}


def compute_career_trajectory(text: str, years_experience: int) -> CareerTrajectory:
    text_lower = text.lower()
    notes: List[str] = []
    score = 0

    found_levels = [(label, lvl) for label, lvl in _ROLE_LEVELS.items() if label in text_lower]
    if len(found_levels) >= 2:
        sorted_levels = sorted(found_levels, key=lambda x: x[1])
        lowest, highest = sorted_levels[0], sorted_levels[-1]
        delta = highest[1] - lowest[1]
        if delta >= 3:
            score += 30
            notes.append(f"{lowest[0].title()} → {highest[0].title()} progression (+30%)")
        elif delta >= 1:
            score += 15
            notes.append(f"{lowest[0].title()} → {highest[0].title()} growth (+15%)")
    elif found_levels:
        notes.append(f"Role level: {found_levels[0][0].title()}")

    has_frontend = any(kw in text_lower for kw in ["react", "vue", "angular", "html", "css", "frontend"])
    has_backend = any(kw in text_lower for kw in ["python", "node", "java", "django", "fastapi", "backend"])
    if has_frontend and has_backend:
        score += 22
        notes.append("Frontend → Fullstack breadth detected (+22%)")

    has_ml = any(kw in text_lower for kw in ["machine learning", "deep learning", "tensorflow", "pytorch", "nlp"])
    has_eng = any(kw in text_lower for kw in ["python", "java", "c++", "software engineer"])
    if has_ml and has_eng:
        score += 18
        notes.append("Engineering + ML/AI skill fusion (+18%)")

    if years_experience >= 5:
        score += 15
        notes.append(f"{years_experience}+ years of experience (+15%)")
    elif years_experience >= 2:
        score += 8
        notes.append(f"{years_experience} years experience (+8%)")

    long_tenure_pattern = re.findall(r"(\d{4})\s*[-–]\s*(?:present|current|\d{4})", text_lower)
    if long_tenure_pattern:
        try:
            durations = []
            for match in re.finditer(r"(\d{4})\s*[-–]\s*(present|current|(\d{4}))", text_lower):
                start = int(match.group(1))
                end = 2025 if match.group(2) in ("present", "current") else int(match.group(3))
                durations.append(end - start)
            if durations and max(durations) >= 3:
                notes.append(f"Stable long-term tenure detected ({max(durations)}yr max)")
        except Exception:
            pass

    score = max(0, min(100, score))
    label = "Rising" if score >= 60 else "Stable" if score >= 30 else "Early"
    return CareerTrajectory(score=score, label=label, notes=notes)


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 5 — FAKE EXPERIENCE DETECTOR
# ─────────────────────────────────────────────────────────────────────────────

_BUZZWORDS_TRUST = [
    "innovative", "synergy", "leverage", "paradigm", "proactive", "go-getter",
    "results-driven", "detail-oriented", "thought leader", "dynamic", "passionate",
    "guru", "ninja", "rockstar", "wizard", "disrupting", "revolutionary",
]


def compute_trust_score(
    text: str,
    years_experience: int,
    github_analysis: GitHubPortfolioAnalysis,
) -> TrustScore:
    score = 100
    flags: List[str] = []
    text_lower = text.lower()
    words = text_lower.split()
    word_count = max(len(words), 1)

    # Buzzword ratio check
    buzz_count = sum(1 for bw in _BUZZWORDS_TRUST if bw in text_lower)
    buzz_ratio = buzz_count / word_count * 100
    if buzz_ratio > 5:
        score -= 8
        flags.append(f"High buzzword ratio ({buzz_ratio:.1f}%) — substance unclear")

    # GitHub mismatch: claims skills but no repos
    claimed_tech = [s for s in ["python", "java", "react", "node", "docker"] if s in text_lower]
    if claimed_tech and github_analysis.verified and github_analysis.total_public_repos == 0:
        score -= 15
        flags.append(f"Claims {claimed_tech[0].title()} but GitHub has 0 public repos")

    # Inflated experience: junior role but claims 5+ years
    is_junior_role = any(kw in text_lower for kw in ["intern", "trainee", "junior"])
    if is_junior_role and years_experience >= 5:
        score -= 10
        flags.append(f"Junior role indicators with {years_experience}yr experience claim — verify dates")

    # Graduation vs work timeline check
    grad_years = re.findall(r"(?:graduated|batch|passed out|class of)\s*[:\-]?\s*(20\d{2}|19\d{2})", text_lower)
    work_years = re.findall(r"(20\d{2})\s*[-–]\s*(?:present|current)", text_lower)
    if grad_years and work_years:
        try:
            grad_year = max(int(y) for y in grad_years)
            work_start = min(int(y) for y in work_years)
            if work_start < grad_year - 1:
                score -= 12
                flags.append(f"Work start ({work_start}) before graduation ({grad_year}) — timeline inconsistency")
        except Exception:
            pass

    # Very short resume is suspicious
    if word_count < 100:
        score -= 10
        flags.append("Very short resume — key details may be missing")

    score = max(0, min(100, score))
    label = "High" if score >= 80 else "Medium" if score >= 60 else "Low"
    badge = "🟢" if score >= 80 else "🟡" if score >= 60 else "🔴"
    return TrustScore(score=score, label=label, badge=badge, flags=flags)


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 6 — AUTO INTERVIEW QUESTIONS
# ─────────────────────────────────────────────────────────────────────────────

_STATIC_QUESTIONS: Dict[str, List[str]] = {
    "python": [
        "Explain the difference between lists and tuples in Python.",
        "How does Python's GIL affect multi-threading?",
        "Describe a time you used generators or decorators in production.",
    ],
    "react": [
        "What is the difference between controlled and uncontrolled components?",
        "Explain the useEffect cleanup pattern with an example.",
        "How do you optimise a React app that renders slowly?",
    ],
    "docker": [
        "Walk me through a multi-stage Docker build you have written.",
        "How do you handle secrets in Docker containers securely?",
        "Describe the difference between CMD and ENTRYPOINT.",
    ],
    "machine learning": [
        "How do you handle class imbalance in a classification problem?",
        "Explain bias-variance tradeoff with a real example from your work.",
        "Which feature selection strategies have you used and why?",
    ],
    "sql": [
        "Write a query to find the second-highest salary in a table.",
        "Explain the difference between INNER JOIN and LEFT JOIN.",
        "How would you optimise a slow-running query?",
    ],
    "aws": [
        "What is the difference between S3 and EBS storage?",
        "How do you architect a highly available service on AWS?",
        "Describe your experience with IAM roles and policies.",
    ],
    "kubernetes": [
        "Explain the difference between a Deployment and a StatefulSet.",
        "How do you roll back a broken Kubernetes deployment?",
        "What is a liveness probe and when would you use it?",
    ],
    "node": [
        "How does the Node.js event loop work?",
        "Explain the difference between Promise.all and Promise.allSettled.",
        "How do you manage memory leaks in a Node.js service?",
    ],
    "java": [
        "What is the difference between HashMap and ConcurrentHashMap?",
        "Explain the Java memory model and garbage collection.",
        "How do you use streams and lambdas for collection processing?",
    ],
    "typescript": [
        "What are the benefits of using TypeScript over JavaScript?",
        "Explain generics in TypeScript with a practical example.",
        "How do you handle strict null checks in TypeScript?",
    ],
}

_GENERIC_QUESTIONS = [
    "Describe the most technically complex project in your portfolio.",
    "How do you approach debugging a production issue at 2 AM?",
    "Walk me through your git workflow in a team environment.",
    "How do you stay current with new technologies in your field?",
    "Describe a situation where you had to refactor legacy code.",
]


def generate_interview_questions(
    candidate_name: str,
    matched_skills: List[str],
    jd_text: str,
) -> InterviewQuestions:
    questions: List[str] = []
    generated_by = "rule-based"

    # Pull skill-specific questions (up to 2 per skill, max 3 skills)
    used_skills = 0
    for skill in matched_skills:
        if used_skills >= 3:
            break
        skill_lower = skill.lower()
        for key, qs in _STATIC_QUESTIONS.items():
            if key in skill_lower or skill_lower in key:
                questions.extend(qs[:2])
                used_skills += 1
                break

    # Pad with generic questions up to 8 total
    for q in _GENERIC_QUESTIONS:
        if len(questions) >= 8:
            break
        if q not in questions:
            questions.append(q)

    # Try Groq for enhanced questions if available
    if _groq_client and matched_skills:
        try:
            skills_str = ", ".join(matched_skills[:5])
            response = _groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{
                    "role": "user",
                    "content": (
                        f"Generate 5 precise technical interview questions for a candidate named {candidate_name} "
                        f"who has skills in: {skills_str}.\n"
                        "Make questions specific, practical, and senior-level.\n"
                        "Return a JSON object: {\"questions\": [\"Q1\", \"Q2\", ...]}\n"
                        "Return ONLY valid JSON, no explanation."
                    ),
                }],
                temperature=0.4,
                max_tokens=400,
            )
            raw = (response.choices[0].message.content or "").strip()
            json_match = re.search(r"\{.*?\}", raw, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                llm_qs = data.get("questions", [])
                if isinstance(llm_qs, list) and llm_qs:
                    questions = [str(q) for q in llm_qs[:5]] + questions[5:]
                    generated_by = "llm-groq"
                    LLM_STATS["total_calls"] += 1
        except Exception as exc:
            logger.warning("Groq interview questions failed: %s", exc)

    return InterviewQuestions(questions=questions[:8], generated_by=generated_by)


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


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 8 — RESUME IMPROVEMENT ADVISOR
# ─────────────────────────────────────────────────────────────────────────────

def generate_resume_advice(
    ats: ATSScore,
    trust: TrustScore,
    career: CareerTrajectory,
    matched_skills: List[str],
    missing_skills: List[str],
    github_analysis: GitHubPortfolioAnalysis,
    fit_score: float,
) -> ResumeAdvice:
    advice: List[str] = []

    # ATS fixes first (highest ROI)
    for suggestion in ats.suggestions[:2]:
        advice.append(suggestion)

    # Trust issues
    for flag in trust.flags[:1]:
        advice.append(f"Review: {flag}")

    # Missing skills
    if missing_skills:
        top_missing = missing_skills[:3]
        advice.append(f"Add these missing skills to your resume: {', '.join(s.title() for s in top_missing)}")

    # GitHub
    if not github_analysis.verified:
        advice.append("Link an active GitHub profile — adds up to +15 fit score points")
    elif github_analysis.jd_relevant_projects == 0:
        advice.append("Pin JD-relevant repositories on GitHub for stronger portfolio evidence")
    elif github_analysis.best_project_complexity < 5:
        advice.append("Add README files and tests to your top GitHub projects to boost complexity score")

    # Career progression
    if career.score < 30 and career.label == "Early":
        advice.append("Include internships, side projects, or open-source contributions to show progression")

    # Generic if nothing else
    if not advice:
        advice.append("Profile is well-aligned with the JD. Maintain active GitHub contributions.")

    priority_fix = advice[0] if advice else "No critical fixes needed."
    return ResumeAdvice(advice=advice[:6], priority_fix=priority_fix)


def build_analytics(results: List[CandidateResult], required_skills: List[str]) -> AnalysisAnalytics:
    if not results:
        return AnalysisAnalytics(
            resumes_uploaded=0,
            average_fit_score=0.0,
            candidates_above_80=0,
            score_distribution={"top": 0, "middle": 0, "low": 0},
            skill_coverage=[],
            candidate_scores=[],
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

    return AnalysisAnalytics(
        resumes_uploaded=len(results),
        average_fit_score=round(sum(fit_scores) / len(fit_scores), 2),
        candidates_above_80=distribution["top"],
        score_distribution=distribution,
        skill_coverage=skill_coverage,
        candidate_scores=candidate_scores,
    )


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
        "Preparing ranking dashboard payload",
    ]

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

    results: List[CandidateResult] = []

    for index, resume in enumerate(resume_documents):
        resume_text_lower = resume["text"].lower()

        matched_skills = [skill for skill in required_skills if skill in resume_text_lower]
        missing_skills = [skill for skill in required_skills if skill not in resume_text_lower]

        skills_match_score = (
            round((len(matched_skills) / len(required_skills)) * 100, 2)
            if required_skills
            else round(min(100.0, similarity_scores[index] * 100), 2)
        )

        candidate_years = extract_experience_years(resume["text"])
        candidate_education_level = extract_education_level(resume["text"])
        experience_match_score = calculate_experience_score(candidate_years, jd_years)
        education_match_score = calculate_education_score(
            candidate_education_level, jd_education_level
        )

        links = unique_links(
            [
                *resume.get("extracted_links", []),
                *extract_urls(resume["text"]),
                *infer_social_urls_from_text(resume["text"]),
            ]
        )
        github_urls = [link for link in links if classify_link_type(link) == "github"]
        linkedin_urls = [link for link in links if classify_link_type(link) == "linkedin"]
        portfolio_urls = [link for link in links if classify_link_type(link) == "portfolio"]

        github_url = github_urls[0] if github_urls else None
        linkedin_url = linkedin_urls[0] if linkedin_urls else None
        portfolio_url = portfolio_urls[0] if portfolio_urls else None

        github_verification = await asyncio.to_thread(
            verify_github_portfolio,
            github_url,
            required_skills,
            nice_to_have_skills,
        )
        github_activity = github_verification["github_activity"]
        github_analysis = github_verification["github_analysis"]

        linkedin_analysis = await asyncio.to_thread(
            verify_linkedin_portfolio,
            linkedin_url,
            required_skills,
            nice_to_have_skills,
        )
        smart_portfolio = build_smart_portfolio_summary(
            github_analysis=github_analysis,
            linkedin_analysis=linkedin_analysis,
        )

        scanned_links: List[LinkScanResult] = []
        linkedin_valid = False
        linkedin_reachable = False
        portfolio_reachable = False

        for url in links:
            link_type = classify_link_type(url)
            if link_type == "linkedin":
                linkedin_check = await asyncio.to_thread(verify_linkedin, url)
                linkedin_valid = linkedin_valid or linkedin_check["valid"]
                linkedin_reachable = linkedin_reachable or linkedin_check["reachable"]
                scanned_links.append(
                    LinkScanResult(
                        url=url,
                        link_type=link_type,
                        reachable=linkedin_check["reachable"],
                        valid_format=linkedin_check["valid"],
                        status_code=linkedin_check.get("status_code"),
                        notes=linkedin_check.get("notes", ""),
                    )
                )
            elif link_type == "portfolio":
                portfolio_check = await asyncio.to_thread(verify_portfolio, url)
                portfolio_reachable = portfolio_reachable or portfolio_check["reachable"]
                scanned_links.append(
                    LinkScanResult(
                        url=url,
                        link_type=link_type,
                        reachable=portfolio_check["reachable"],
                        valid_format=True,
                        status_code=portfolio_check.get("status_code"),
                        notes=portfolio_check.get("notes", ""),
                    )
                )
            elif link_type == "github":
                github_check = await asyncio.to_thread(verify_generic_link, url)
                scanned_links.append(
                    LinkScanResult(
                        url=url,
                        link_type=link_type,
                        reachable=github_check["reachable"],
                        valid_format=bool(extract_github_username(url)),
                        status_code=github_check.get("status_code"),
                        notes=github_check.get("notes", ""),
                    )
                )
            else:
                generic_check = await asyncio.to_thread(verify_generic_link, url)
                scanned_links.append(
                    LinkScanResult(
                        url=url,
                        link_type=link_type,
                        reachable=generic_check["reachable"],
                        valid_format=True,
                        status_code=generic_check.get("status_code"),
                        notes=generic_check.get("notes", ""),
                    )
                )

        activity_bonus = smart_portfolio.verification_bonus
        if linkedin_valid and linkedin_reachable:
            activity_bonus += 2
        if portfolio_reachable:
            activity_bonus += 2

        activity_bonus = round(min(65.0, activity_bonus), 2)
        similarity_score = round(float(similarity_scores[index] * 100), 2)

        fit_score = round(
            min(
                100.0,
                (0.45 * similarity_score)
                + (0.30 * skills_match_score)
                + (0.15 * experience_match_score)
                + (0.10 * education_match_score)
                + activity_bonus,
            ),
            2,
        )

        suggestions: List[str] = []
        if missing_skills:
            suggestions.append(f"Missing key skills: {', '.join(missing_skills[:6])}")
        if candidate_years < jd_years and jd_years > 0:
            suggestions.append(
                f"Experience below requirement ({candidate_years}y vs {jd_years}y target)"
            )
        if not github_url:
            suggestions.append("Add an active GitHub profile for stronger technical validation")
        if not smart_portfolio.stack_experience_verified:
            suggestions.append(
                "Portfolio evidence is weak for JD stack. Add clearer project links and recent contributions."
            )

        # ── 8 new features ──────────────────────────────────────────────────
        resume_file_bytes = resume.get("file_bytes")
        resume_filename = resume.get("source_file", "")

        ats = compute_ats_score(resume["text"], resume_file_bytes, resume_filename)
        bias = compute_bias_flags(resume["candidate_name"], resume["text"])
        career = compute_career_trajectory(resume["text"], candidate_years)
        trust = compute_trust_score(resume["text"], candidate_years, github_analysis)
        interview_qs = generate_interview_questions(
            resume["candidate_name"], matched_skills, final_jd_text
        )
        email_tpl = generate_email_template(
            resume["candidate_name"], fit_score, matched_skills, missing_skills,
            github_activity.username,
        )
        advice = generate_resume_advice(
            ats, trust, career, matched_skills, missing_skills, github_analysis, fit_score
        )
        # ────────────────────────────────────────────────────────────────────

        results.append(
            CandidateResult(
                candidate_id=str(uuid.uuid4()),
                candidate_name=resume["candidate_name"],
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
                bias_flags=bias,
                career_trajectory=career,
                trust_score=trust,
                interview_questions=interview_qs,
                email_template=email_tpl,
                resume_advice=advice,
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
            )
        )

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
                "similarity_score": candidate.get("similarity_score"),
                "skills_match_score": candidate.get("skills_match_score"),
                "experience_match_score": candidate.get("experience_match_score"),
                "education_match_score": candidate.get("education_match_score"),
                "matched_skills": ", ".join(candidate.get("matched_skills", [])),
                "missing_skills": ", ".join(candidate.get("missing_skills", [])),
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
            }
        )

    csv_buffer = io.StringIO()
    pd.DataFrame(rows).to_csv(csv_buffer, index=False)
    csv_buffer.seek(0)

    headers = {
        "Content-Disposition": f"attachment; filename=resume_screening_{batch_id}.csv"
    }
    return StreamingResponse(iter([csv_buffer.getvalue()]), media_type="text/csv", headers=headers)


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