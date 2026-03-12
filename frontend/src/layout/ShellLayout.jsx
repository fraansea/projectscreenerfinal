import { BarChart3, FileSearch, Home, LoaderCircle, LogOut, Menu, Plus, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

const navStyles = ({ isActive }) =>
  [
    "inline-flex items-center gap-2 rounded-full border px-4 py-2 text-sm font-medium",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500",
    isActive
      ? "border-[#eb6a45] bg-[#eb6a45] text-white"
      : "border-slate-300 bg-white text-slate-700 hover:border-[#eb6a45] hover:text-[#eb6a45]",
  ].join(" ");

export const ShellLayout = () => {
  const location = useLocation();
  const { recruiter, logout } = useAuth();
  const [batchId, setBatchId] = useState(localStorage.getItem("last_resume_batch_id"));

  useEffect(() => {
    setBatchId(localStorage.getItem("last_resume_batch_id"));
  }, [location.pathname]);

  return (
    <div className="relative min-h-screen px-3 py-4 md:px-6 md:py-7" data-testid="app-shell">
      <div className="premium-shell mx-auto min-h-[92vh] w-full max-w-[1500px] overflow-hidden" data-testid="premium-shell-wrapper">
        <header className="z-20 border-b border-slate-200/80 px-5 py-5 md:px-8" data-testid="main-header">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-3" data-testid="brand-block">
              <button
                className="grid h-11 w-11 place-items-center rounded-full border border-slate-200 bg-white text-slate-700"
                data-testid="shell-menu-button"
                type="button"
              >
                <Menu size={18} />
              </button>

              <div className="grid h-11 w-11 place-items-center rounded-full bg-black text-white" data-testid="brand-icon">
                <FileSearch size={18} />
              </div>
              <div>
                <p className="text-sm text-slate-500" data-testid="brand-label">
                  Recruiter Workspace
                </p>
                <h1 className="text-xl font-semibold text-slate-900" data-testid="brand-title">
                  AI Resume Screener
                </h1>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-3" data-testid="header-utility-actions">
              <button
                type="button"
                className="grid h-11 w-11 place-items-center rounded-full border border-slate-200 bg-white text-slate-800"
                data-testid="header-add-action-button"
              >
                <Plus size={18} />
              </button>

              <div className="flex items-center gap-2 rounded-full border border-slate-200 bg-white px-2 py-1" data-testid="header-profile-pill">
                <img
                  src="https://images.unsplash.com/photo-1494790108377-be9c29b29330?crop=entropy&cs=srgb&fm=jpg&q=85&w=80&h=80"
                  alt="Recruiter"
                  className="h-9 w-9 rounded-full object-cover"
                  data-testid="header-profile-avatar"
                />
                <div className="pr-2">
                  <p className="text-sm font-semibold text-slate-900" data-testid="header-profile-name">{recruiter?.name || "HR Console"}</p>
                  <p className="text-xs text-slate-500" data-testid="header-profile-role">{recruiter?.company || "Recruiter Assistant"}</p>
                </div>
              </div>

              <div className="flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-2" data-testid="header-search-bar">
                <Search size={16} className="text-slate-500" />
                <span className="text-sm text-slate-400" data-testid="header-search-placeholder">Search candidates, skills...</span>
              </div>
            </div>
          </div>

          <nav className="mt-5 flex flex-wrap items-center gap-2" data-testid="top-navigation">
            <NavLink to="/dashboard" className={navStyles} data-testid="nav-dashboard-link">
              <Home size={16} /> Dashboard
            </NavLink>

            <NavLink to="/upload" className={navStyles} data-testid="nav-upload-link">
              <FileSearch size={16} /> Upload
            </NavLink>

            <NavLink
              to={batchId ? `/results/${batchId}` : "/upload"}
              className={navStyles}
              data-testid="nav-results-link"
            >
              <LoaderCircle size={16} /> Results
            </NavLink>

            <NavLink
              to={batchId ? `/analytics/${batchId}` : "/upload"}
              className={navStyles}
              data-testid="nav-analytics-link"
            >
              <BarChart3 size={16} /> Analytics
            </NavLink>

            <button
              type="button"
              onClick={logout}
              className="inline-flex items-center gap-2 rounded-full border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:border-red-300 hover:text-red-600"
              data-testid="logout-button"
            >
              <LogOut size={16} /> Logout {recruiter?.name ? `(${recruiter.name})` : ""}
            </button>
          </nav>
        </header>

        <main className="relative z-10 px-5 py-7 md:px-8" data-testid="main-content">
          <div className="mx-auto w-full max-w-[1420px]">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
};
