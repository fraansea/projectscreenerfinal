import axios from "axios";

const _rawBase = (process.env.REACT_APP_BACKEND_URL || "").trim().replace(/\/$/, "");
const _isDev = process.env.NODE_ENV === "development";

/**
 * In development, use same-origin `/api` so the webpack proxy (craco) forwards to FastAPI.
 * That fixes auth when you open the app as http://192.168.x.x:3000 — `localhost:8000` would
 * point at the wrong machine otherwise.
 * In production, use REACT_APP_BACKEND_URL (full origin, no /api).
 */
export const API_ORIGIN =
  _isDev && typeof window !== "undefined"
    ? window.location.origin
    : _rawBase || "http://localhost:8000";

const API_BASE = _isDev ? "/api" : `${_rawBase || "http://localhost:8000"}/api`;

if (!_isDev && !_rawBase && typeof console !== "undefined") {
  // eslint-disable-next-line no-console
  console.warn("[PIXLS] Set REACT_APP_BACKEND_URL for production builds.");
}

const withAuth = (token) =>
  token
    ? {
        Authorization: `Bearer ${token}`,
      }
    : {};

export const signupRecruiter = async ({ name, email, company, role, password, confirmPassword }) => {
  const { data } = await axios.post(`${API_BASE}/auth/recruiters/signup`, {
    name,
    email,
    company,
    role,
    password,
    confirm_password: confirmPassword,
  });
  return data;
};

export const loginRecruiter = async ({ email, password, rememberMe }) => {
  const { data } = await axios.post(`${API_BASE}/auth/recruiters/login`, {
    email,
    password,
    remember_me: rememberMe,
  });
  return data;
};

export const getRecruiterMe = async (token) => {
  const { data } = await axios.get(`${API_BASE}/auth/recruiters/me`, {
    headers: withAuth(token),
  });
  return data;
};

export const startResumeAnalysis = async ({ jdText, jdFile, zipFile, token }) => {
  const formData = new FormData();
  if (jdText?.trim()) {
    formData.append("jd_text", jdText.trim());
  }
  if (jdFile) {
    formData.append("jd_file", jdFile);
  }
  formData.append("resumes_zip", zipFile);

  const { data } = await axios.post(`${API_BASE}/screener/analyze/start`, formData, {
    headers: { "Content-Type": "multipart/form-data", ...withAuth(token) },
  });

  return data;
};

export const getBatchStatus = async (batchId, token) => {
  const { data } = await axios.get(`${API_BASE}/screener/status/${batchId}`, {
    headers: withAuth(token),
  });
  return data;
};

export const getResults = async (batchId, token) => {
  const { data } = await axios.get(`${API_BASE}/screener/results/${batchId}`, {
    headers: withAuth(token),
  });
  return data;
};

export const getAnalytics = async (batchId, token) => {
  const { data } = await axios.get(`${API_BASE}/screener/analytics/${batchId}`, {
    headers: withAuth(token),
  });
  return data;
};

export const getExportUrl = (batchId, token) => {
  const params = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${API_BASE}/screener/export/${batchId}${params}`;
};

export const getHeatmap = async (batchId, token) => {
  const { data } = await axios.get(`${API_BASE}/screener/heatmap/${batchId}`, {
    headers: withAuth(token),
  });
  return data;
};

export const setCandidateLabel = async ({ batchId, candidateId, label, token }) => {
  const { data } = await axios.post(
    `${API_BASE}/screener/labels`,
    { batch_id: batchId, candidate_id: candidateId, label },
    { headers: withAuth(token) }
  );
  return data;
};

export const getBatchEvaluation = async (batchId, token) => {
  const { data } = await axios.get(`${API_BASE}/screener/eval/${batchId}`, {
    headers: withAuth(token),
  });
  return data;
};

export const getScreeningBatches = async (token) => {
  const { data } = await axios.get(`${API_BASE}/screener/batches`, {
    headers: withAuth(token),
  });
  return data;
};

export const sendEmail = async ({ to, subject, body, cc, bcc, template_type, candidate_id, candidate_name }, token) => {
  const { data } = await axios.post(
    `${API_BASE}/email/send`,
    { to, subject, body, cc, bcc, template_type, candidate_id, candidate_name },
    { headers: withAuth(token) }
  );
  return data;
};

export const getEmailHistory = async (token) => {
  const { data } = await axios.get(`${API_BASE}/email/history`, {
    headers: withAuth(token),
  });
  return data;
};
