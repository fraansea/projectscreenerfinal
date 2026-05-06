import { useEffect, useMemo, useState } from "react";
import { ArrowRight, Eye, EyeOff, Search } from "lucide-react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { toast } from "../components/ui/sonner";
import { useAuth } from "../context/AuthContext";
import { loginRecruiter, signupRecruiter } from "../services/api";

const heroImage =
  "https://images.unsplash.com/photo-1637249820580-a877474a889d?crop=entropy&cs=srgb&fm=jpg&q=85";

export default function AuthPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { persistAuth, isAuthenticated, isReady } = useAuth();
  const isSignup = location.pathname.includes("signup");

  const [showPassword, setShowPassword] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [form, setForm] = useState({
    name: "",
    email: "",
    company: "",
    role: "",
    password: "",
    confirmPassword: "",
    rememberMe: true,
  });

  const pageTitle = useMemo(
    () => (isSignup ? "Create recruiter account" : "Login to your recruiter account"),
    [isSignup],
  );

  const onChange = (key, value) => {
    setForm((old) => ({ ...old, [key]: value }));
  };

  useEffect(() => {
    if (isReady && isAuthenticated) {
      navigate("/dashboard", { replace: true });
    }
  }, [isAuthenticated, isReady, navigate]);

  const handleSubmit = async (event) => {
    event.preventDefault();
    setIsSubmitting(true);
    try {
      if (isSignup) {
        if (form.password !== form.confirmPassword) {
          toast.error("Password and confirm password must match.");
          return;
        }
        const response = await signupRecruiter({
          name: form.name,
          email: form.email,
          company: form.company,
          role: form.role,
          password: form.password,
          confirmPassword: form.confirmPassword,
        });
        persistAuth(response.access_token, response.recruiter, true);
        toast.success("Recruiter account created successfully.");
      } else {
        const response = await loginRecruiter({
          email: form.email,
          password: form.password,
          rememberMe: form.rememberMe,
        });
        persistAuth(response.access_token, response.recruiter, form.rememberMe);
        toast.success("Welcome back recruiter.");
      }

      const nextRoute = location.state?.from || "/dashboard";
      navigate(nextRoute, { replace: true });
    } catch (error) {
      const status = error?.response?.status;
      const detail = error?.response?.data?.detail;
      const isNetwork = !error?.response && error?.message;
      let message = detail || "Unable to continue authentication.";
      if (isNetwork) {
        message =
          "Cannot reach the API server. Start the backend (port 8000) and refresh, or open the app at http://localhost:3000.";
      } else if (status === 401) {
        message = detail || "Invalid email or password.";
      }
      toast.error(typeof message === "string" ? message : "Authentication failed.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <section className="min-h-screen px-3 py-5 md:px-7 md:py-8" data-testid="auth-page">
      <div className="premium-shell mx-auto min-h-[92vh] max-w-[1500px] border-none p-4 md:p-6" data-testid="auth-shell-wrapper">
        <header className="rounded-2xl border border-slate-200 bg-white/85 px-4 py-3" data-testid="auth-header">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-8" data-testid="auth-header-left">
              <p className="text-2xl font-semibold text-slate-900" data-testid="auth-brand-title">PIXLS</p>
              <nav className="hidden items-center gap-5 text-sm text-slate-500 md:flex" data-testid="auth-header-nav">
                <span data-testid="auth-nav-home">Home</span>
                <span className="font-semibold text-slate-900" data-testid="auth-nav-start">Get started</span>
                <span data-testid="auth-nav-about">About</span>
                <span data-testid="auth-nav-forum">Forum</span>
              </nav>
            </div>
            <div className="flex items-center gap-3" data-testid="auth-header-right">
              <div className="hidden items-center gap-2 rounded-full border border-slate-200 bg-slate-100 px-4 py-2 text-sm text-slate-500 md:flex" data-testid="auth-header-search">
                <Search size={16} /> Try "Recruiter trends"
              </div>
              <Link to={isSignup ? "/login" : "/signup"} data-testid="auth-switch-top-link">
                <button
                  type="button"
                  className="rounded-full bg-[#eb6a45] px-6 py-2 text-sm font-semibold text-white hover:bg-[#d7552f]"
                  data-testid="auth-switch-top-button"
                >
                  {isSignup ? "Login" : "Signup"}
                </button>
              </Link>
            </div>
          </div>
        </header>

        <div className="mt-5 grid gap-5 xl:grid-cols-[1fr_1.2fr]" data-testid="auth-body-grid">
          <div className="premium-card border-none p-6 md:p-9" data-testid="auth-left-panel">
            <p className="text-sm uppercase tracking-[0.16em] text-slate-500" data-testid="auth-left-tag">
              Largest Hiring Source
            </p>
            <h1 className="mt-4 text-5xl leading-tight text-slate-900" data-testid="auth-left-heading">
              Powered by
              <br />
              <span className="bg-[#f6b7a3] px-2">Recruiters around</span>
              <br />
              the world.
            </h1>
            <p className="mt-8 text-base text-slate-500" data-testid="auth-left-subtext">
              Secure recruiter access for candidate screening, ranking analytics, and portfolio verification.
            </p>

            <div className="mt-8" data-testid="auth-left-switch-block">
              <p className="text-sm text-slate-500" data-testid="auth-left-switch-text">
                {isSignup ? "Already have account?" : "Don’t have account?"}
              </p>
              <Link
                to={isSignup ? "/login" : "/signup"}
                className="mt-2 inline-flex items-center gap-2 border-b border-slate-800 pb-1 text-base font-semibold text-slate-900"
                data-testid="auth-left-switch-link"
              >
                {isSignup ? "Login now" : "Create account"} <ArrowRight size={16} />
              </Link>
            </div>

            <div className="mt-8 overflow-hidden rounded-3xl" data-testid="auth-left-image-wrap">
              <img src={heroImage} alt="Recruiter visual" className="h-[170px] w-full object-cover" data-testid="auth-left-image" />
            </div>
          </div>

          <div className="premium-card border-none p-4 md:p-6" data-testid="auth-right-panel">
            <div className="mb-4 overflow-hidden rounded-2xl border border-slate-200" data-testid="auth-right-image-strip-wrap">
              <img
                src={heroImage}
                alt="Resume screening visual"
                className="h-[140px] w-full object-cover object-center"
                data-testid="auth-right-image-strip"
              />
            </div>

            <div className="flex items-center justify-center">
              <form className="w-full max-w-[520px] rounded-3xl border border-slate-200 bg-white p-6 md:p-7" onSubmit={handleSubmit} data-testid="auth-form">
                <p className="text-center text-2xl font-semibold text-slate-900" data-testid="auth-form-title">
                  {pageTitle}
                </p>

                <div className="mt-5 space-y-4">
                  {isSignup && (
                    <>
                      <div data-testid="signup-name-field-wrap">
                        <label className="text-xs font-semibold text-slate-500" data-testid="signup-name-label">Name</label>
                        <input type="text" value={form.name} onChange={(event) => onChange("name", event.target.value)} className="mt-1 h-11 w-full rounded-xl border border-slate-200 px-3" required data-testid="signup-name-input" />
                      </div>
                      <div data-testid="signup-company-field-wrap">
                        <label className="text-xs font-semibold text-slate-500" data-testid="signup-company-label">Company</label>
                        <input type="text" value={form.company} onChange={(event) => onChange("company", event.target.value)} className="mt-1 h-11 w-full rounded-xl border border-slate-200 px-3" required data-testid="signup-company-input" />
                      </div>
                      <div data-testid="signup-role-field-wrap">
                        <label className="text-xs font-semibold text-slate-500" data-testid="signup-role-label">Role</label>
                        <input type="text" value={form.role} onChange={(event) => onChange("role", event.target.value)} className="mt-1 h-11 w-full rounded-xl border border-slate-200 px-3" required data-testid="signup-role-input" />
                      </div>
                    </>
                  )}

                  <div data-testid="auth-email-field-wrap">
                    <label className="text-xs font-semibold text-slate-500" data-testid="auth-email-label">Email</label>
                    <input type="email" value={form.email} onChange={(event) => onChange("email", event.target.value)} className="mt-1 h-11 w-full rounded-xl border border-slate-200 px-3" required data-testid="auth-email-input" />
                  </div>

                  <div data-testid="auth-password-field-wrap">
                    <label className="text-xs font-semibold text-slate-500" data-testid="auth-password-label">Password</label>
                    <div className="relative mt-1">
                      <input type={showPassword ? "text" : "password"} value={form.password} onChange={(event) => onChange("password", event.target.value)} className="h-11 w-full rounded-xl border border-slate-200 px-3 pr-12" required data-testid="auth-password-input" />
                      <button type="button" onClick={() => setShowPassword((old) => !old)} className="absolute right-3 top-2.5 text-slate-500" data-testid="auth-toggle-password-button">
                        {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                      </button>
                    </div>
                  </div>

                  {isSignup && (
                    <div data-testid="signup-confirm-password-field-wrap">
                      <label className="text-xs font-semibold text-slate-500" data-testid="signup-confirm-password-label">Confirm Password</label>
                      <input type={showPassword ? "text" : "password"} value={form.confirmPassword} onChange={(event) => onChange("confirmPassword", event.target.value)} className="mt-1 h-11 w-full rounded-xl border border-slate-200 px-3" required data-testid="signup-confirm-password-input" />
                    </div>
                  )}

                  {!isSignup && (
                    <div className="flex items-center justify-between" data-testid="login-options-row">
                      <label className="inline-flex items-center gap-2 text-sm text-slate-600" data-testid="login-remember-checkbox-wrap">
                        <input type="checkbox" checked={form.rememberMe} onChange={(event) => onChange("rememberMe", event.target.checked)} data-testid="login-remember-checkbox" />
                        Remember me
                      </label>
                      <span className="text-sm text-slate-500" data-testid="login-forgot-password-link">Forgot your password?</span>
                    </div>
                  )}
                </div>

                <button
                  type="submit"
                  disabled={isSubmitting}
                  className="mt-6 h-11 w-full rounded-full bg-[#eb6a45] text-sm font-semibold text-white hover:bg-[#d7552f]"
                  data-testid="auth-submit-button"
                >
                  {isSubmitting ? "Please wait..." : isSignup ? "Create recruiter account" : "Login"}
                </button>
              </form>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
