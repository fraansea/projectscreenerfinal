# PRD: AI‑Powered Resume Screener with Bulk Ranking & Link Activity Analysis

## Original Problem Statement
Build a modern full-stack web application for recruiters/HR teams to upload multiple resumes (PDF/DOCX) in ZIP format, compare them against a job description, generate fit-score ranking with tiering, show matched/missing skills, verify external links (GitHub/LinkedIn/portfolio), and provide analytics with CSV export.

## Architecture Decisions
- **Frontend:** React (multi-route flow: Upload → Processing → Results → Analytics), Shadcn UI components, Recharts for analytics, Framer Motion for staged transitions.
- **Backend:** FastAPI with async endpoints and Motor for MongoDB persistence.
- **Database:** MongoDB collection `screener_batches` storing batch payloads (results + analytics + logs).
- **NLP/Scoring:** TF-IDF + cosine similarity, weighted composite fit score, sub-scores for skills/experience/education, tier classification.
- **File processing:** ZIP extraction with PDF/DOCX/TXT parsing (`PyPDF2`, `python-docx`), malformed ZIP defensive validation.
- **Link verification:** Lightweight URL detection + GitHub API profile/activity checks + basic LinkedIn/portfolio reachability.

## User Personas
- **HR Recruiter:** Needs quick, explainable shortlist from high volume resumes.
- **Talent Acquisition Lead:** Needs ranking consistency and dashboard summary metrics.
- **Hiring Manager (viewer):** Needs detail-level justification (skills gaps/activity context) for candidate comparison.

## Core Requirements (Static)
1. Bulk upload resumes via ZIP.
2. JD input via text or file.
3. Resume-to-JD similarity and weighted scoring.
4. Tiering: Top / Middle / Low.
5. Candidate-level skill-gap insights.
6. External link validation and GitHub activity bonus.
7. Results filtering/sorting/search + detail expansion.
8. Analytics dashboard and CSV export.

## What’s Implemented
### 2026-03-11
- Implemented backend API endpoints:
  - `POST /api/screener/analyze`
  - `GET /api/screener/results/{batch_id}`
  - `GET /api/screener/analytics/{batch_id}`
  - `GET /api/screener/export/{batch_id}`
- Implemented NLP resume scoring pipeline with:
  - JD keyword/skill extraction
  - TF-IDF cosine similarity
  - Skills/experience/education sub-scores
  - Final fit score + automatic tiering
- Implemented external link scanning:
  - GitHub username validation, repo stats, last active, top languages
  - Basic LinkedIn format/reachability checks
  - Portfolio reachability checks
- Enhanced CV link analysis to scan all URLs per resume (including multiple LinkedIn links) with per-link status summary.
- Added **Smart Portfolio Verifier**:
  - GitHub project analysis with project type classification and complexity scoring (Beginner/Intermediate/Advanced)
  - JD stack experience validation via coverage metrics, JD-relevant project count, and portfolio verification bonus
  - LinkedIn public profile keyword/project signals with weighted verification contribution
  - Results UI now shows Smart Portfolio summary + Top JD-relevant projects + enriched CSV export metrics
- Added **3-layer hidden-link extraction** for modern resumes:
  - Layer 1: advanced text/handle inference (e.g., `GitHub Link: username`, `LinkedIn: username`)
  - Layer 2: PDF annotation/hyperlink extraction (captures clickable button links missed by plain text extraction)
  - Layer 3: OCR fallback path for PDF pages when link text is image-based
  - Parsed/inferred links are merged into candidate link scanning pipeline for verification and scoring
- Added **large-batch async processing flow** to prevent timeout for 20+ resumes:
  - New start endpoint + status polling (`/api/screener/analyze/start`, `/api/screener/status/{batch_id}`)
  - Frontend processing page now polls real backend status and transitions to results only after completion
  - In-progress protections for results/analytics/export return proper processing state instead of failing silently
- Applied full **premium light dashboard UI redesign** (Upload, Processing, Results, Analytics) inspired by provided reference image:
  - Soft rounded container system, monochrome cards, orange accent actions, balanced data density
  - Refined top shell with utility controls/profile/search + consistent premium card style
  - Fixed navigation continuity bug so Results/Analytics links retain latest batch route reliably across page changes
- Implemented **Recruiter Authentication Module** with reference-inspired UI:
  - New `/login` and `/signup` pages with premium hero layout and recruiter-focused form UX
  - Backend JWT auth APIs for recruiter signup/login/me using MongoDB user records and bcrypt hashing
  - Remember-me session persistence (localStorage) and session-only support (sessionStorage)
  - Protected route flow with logout and post-auth redirect to new recruiter home dashboard (`/dashboard`)
  - Added recruiter dashboard page with quick actions into Upload/Results/Analytics workflow
- Refined auth visuals to match platform identity:
  - Replaced login/signup imagery with resume-screening/device-themed visuals
  - Updated auth page accents to platform palette (light gray + orange `#eb6a45`)
  - Switched to minimal image-strip emphasis for cleaner form-first recruiter experience
- Implemented full frontend recruiter flow:
  - Upload page (JD text/file + ZIP)
  - Processing page (progress + logs)
  - Results dashboard (search/filter/sort/expand/details)
  - Analytics page (bar/pie charts + KPI cards)
- Implemented CSV export action from dashboard.
- Added robust malformed ZIP handling (returns HTTP 400 instead of 500).
- Added backend regression test coverage, including malformed ZIP test.

## Prioritized Backlog
### P0 (Must do next)
- Refactor backend monolith (`server.py`) into modules: parsing, scoring, link-verification, routers.
- Add structured processing job status for truly large ZIPs (non-blocking progress polling).

### P1 (High value)
- Add configurable score weights in UI (skills/experience/education/link bonus).
- Add richer JD extraction using RAKE/skill ontology with role templates.
- Improve LinkedIn verification resilience and safe fallback messaging.

### P2 (Future)
- Bias detection and neutral-scoring diagnostics.
- Email automation for shortlisted candidates.
- Historical batch comparison and trend analytics.

## Next Tasks List
1. Break backend into maintainable modules and service layer.
2. Add background processing with progress polling for bigger resume datasets.
3. Add configurable scoring profile presets per role (Python/Frontend/Data).
4. Add recruiter notes/tagging for shortlisted candidates.
