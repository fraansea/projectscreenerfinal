import axios from "axios";

const API_BASE = `${process.env.REACT_APP_BACKEND_URL}/api`;

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
