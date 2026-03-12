import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { getRecruiterMe } from "../services/api";

const AuthContext = createContext(null);

const TOKEN_STORAGE_KEY = "recruiter_auth_token";
const PROFILE_STORAGE_KEY = "recruiter_profile";

const readStoredToken = () =>
  localStorage.getItem(TOKEN_STORAGE_KEY) || sessionStorage.getItem(TOKEN_STORAGE_KEY);

const readStoredProfile = () => {
  const raw = localStorage.getItem(PROFILE_STORAGE_KEY) || sessionStorage.getItem(PROFILE_STORAGE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
};

export const AuthProvider = ({ children }) => {
  const [token, setToken] = useState(readStoredToken());
  const [recruiter, setRecruiter] = useState(readStoredProfile());
  const [isReady, setIsReady] = useState(false);

  useEffect(() => {
    const bootstrap = async () => {
      if (!token) {
        setIsReady(true);
        return;
      }

      try {
        const me = await getRecruiterMe(token);
        setRecruiter(me);
      } catch {
        localStorage.removeItem(TOKEN_STORAGE_KEY);
        localStorage.removeItem(PROFILE_STORAGE_KEY);
        sessionStorage.removeItem(TOKEN_STORAGE_KEY);
        sessionStorage.removeItem(PROFILE_STORAGE_KEY);
        setToken(null);
        setRecruiter(null);
      } finally {
        setIsReady(true);
      }
    };

    bootstrap();
  }, [token]);

  const persistAuth = (nextToken, nextRecruiter, rememberMe = true) => {
    const storage = rememberMe ? localStorage : sessionStorage;
    const secondary = rememberMe ? sessionStorage : localStorage;

    secondary.removeItem(TOKEN_STORAGE_KEY);
    secondary.removeItem(PROFILE_STORAGE_KEY);

    storage.setItem(TOKEN_STORAGE_KEY, nextToken);
    storage.setItem(PROFILE_STORAGE_KEY, JSON.stringify(nextRecruiter));

    setToken(nextToken);
    setRecruiter(nextRecruiter);
  };

  const logout = () => {
    localStorage.removeItem(TOKEN_STORAGE_KEY);
    localStorage.removeItem(PROFILE_STORAGE_KEY);
    sessionStorage.removeItem(TOKEN_STORAGE_KEY);
    sessionStorage.removeItem(PROFILE_STORAGE_KEY);
    setToken(null);
    setRecruiter(null);
  };

  const value = useMemo(
    () => ({
      token,
      recruiter,
      isAuthenticated: Boolean(token),
      isReady,
      persistAuth,
      logout,
    }),
    [token, recruiter, isReady],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return context;
};
