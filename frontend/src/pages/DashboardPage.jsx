import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  BarChart3,
  BellRing,
  Briefcase,
  Calendar,
  ChevronRight,
  Clock,
  FileUp,
  Layers,
  ShieldCheck,
  Sparkles,
  Star,
  TrendingUp,
  Users,
  Zap,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { getScreeningBatches } from "../services/api";

function ScoreBadge({ score }) {
  const s = Math.round(score || 0);
  const color =
    s >= 80
      ? "bg-emerald-100 text-emerald-700 border-emerald-200"
      : s >= 60
      ? "bg-amber-100 text-amber-700 border-amber-200"
      : "bg-red-100 text-red-700 border-red-200";
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold ${color}`}>
      {s}%
    </span>
  );
}

function TierBadge({ tier }) {
  if (!tier) return null;
  const map = {
    "Strong Hire": "bg-emerald-100 text-emerald-700",
    Hire: "bg-blue-100 text-blue-700",
    Waitlist: "bg-amber-100 text-amber-700",
    Reject: "bg-red-100 text-red-700",
  };
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${map[tier] || "bg-slate-100 text-slate-600"}`}>
      {tier}
    </span>
  );
}

function ProgressBar({ value, max = 100, color = "#eb6a45" }) {
  const pct = Math.min(100, Math.round((value / max) * 100));
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
      <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, backgroundColor: color }} />
    </div>
  );
}

function BatchProjectCard({ batch }) {
  const date = batch.generated_at
    ? new Date(batch.generated_at).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
      })
    : "—";
  const time = batch.generated_at
    ? new Date(batch.generated_at).toLocaleTimeString("en-US", {
        hour: "2-digit",
        minute: "2-digit",
      })
    : "";

  const avgScore = Math.round(batch.average_fit_score || 0);
  const topSkills = (batch.required_skills || []).slice(0, 5);

  return (
    <div className="premium-card border-none flex flex-col gap-4 p-5">
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col">
          <div className="flex items-center gap-2">
            <Briefcase size={15} className="shrink-0 text-[#eb6a45]" />
            <span className="truncate text-sm font-semibold text-slate-800">
              Screening #{batch.batch_id?.slice(-6).toUpperCase()}
            </span>
            {batch.llm_tech_stack_enhanced && (
              <span className="inline-flex items-center gap-1 rounded-full bg-purple-100 px-2 py-0.5 text-[10px] font-medium text-purple-700">
                <Zap size={9} /> AI
              </span>
            )}
          </div>
          <div className="mt-1 flex items-center gap-1 text-xs text-slate-400">
            <Calendar size={11} />
            {date}
            {time && (
              <>
                <Clock size={11} className="ml-1" />
                {time}
              </>
            )}
          </div>
        </div>
        <ScoreBadge score={avgScore} />
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-3 divide-x divide-slate-100 rounded-xl bg-slate-50 text-center text-xs">
        <div className="py-2">
          <p className="text-base font-bold text-slate-900">{batch.resumes_uploaded || 0}</p>
          <p className="text-slate-500">Resumes</p>
        </div>
        <div className="py-2">
          <p className="text-base font-bold text-slate-900">{avgScore}%</p>
          <p className="text-slate-500">Avg Fit</p>
        </div>
        <div className="py-2">
          <p className="text-base font-bold text-slate-900">{batch.candidates_above_80 || 0}</p>
          <p className="text-slate-500">Top Picks</p>
        </div>
      </div>

      {/* Avg score bar */}
      <div>
        <div className="mb-1 flex justify-between text-[11px] text-slate-500">
          <span>Avg Fit Score</span>
          <span>{avgScore}%</span>
        </div>
        <ProgressBar value={avgScore} />
      </div>

      {/* Top candidates */}
      {batch.top_candidates?.length > 0 && (
        <div>
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
            <Star size={10} className="mr-1 inline" />
            Top Candidates
          </p>
          <ul className="space-y-1.5">
            {batch.top_candidates.map((c, i) => (
              <li key={i} className="flex items-center justify-between gap-2">
                <div className="flex min-w-0 items-center gap-1.5">
                  <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-slate-100 text-[10px] font-bold text-slate-600">
                    {i + 1}
                  </span>
                  <span className="truncate text-xs font-medium text-slate-700">{c.name}</span>
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  <TierBadge tier={c.tier} />
                  <ScoreBadge score={c.fit_score} />
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Skills */}
      {topSkills.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {topSkills.map((sk) => (
            <span
              key={sk}
              className="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[10px] font-medium text-slate-600"
            >
              {sk}
            </span>
          ))}
          {(batch.required_skills || []).length > 5 && (
            <span className="rounded-full border border-dashed border-slate-300 px-2 py-0.5 text-[10px] text-slate-400">
              +{(batch.required_skills || []).length - 5} more
            </span>
          )}
        </div>
      )}

      {/* Action links */}
      <div className="mt-auto flex gap-2 pt-1">
        <Link
          to={`/results/${batch.batch_id}`}
          className="flex flex-1 items-center justify-center gap-1 rounded-full bg-[#eb6a45] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[#d45e3a]"
        >
          <Users size={12} /> Results
        </Link>
        <Link
          to={`/analytics/${batch.batch_id}`}
          className="flex flex-1 items-center justify-center gap-1 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:border-[#eb6a45] hover:text-[#eb6a45]"
        >
          <BarChart3 size={12} /> Analytics
        </Link>
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const { recruiter, token } = useAuth();
  const batchId = localStorage.getItem("last_resume_batch_id");

  const [batches, setBatches] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!token) { setLoading(false); return; }
    getScreeningBatches(token)
      .then(setBatches)
      .catch(() => setBatches([]))
      .finally(() => setLoading(false));
  }, [token]);

  const totalResumes = batches.reduce((s, b) => s + (b.resumes_uploaded || 0), 0);
  const avgFit =
    batches.length > 0
      ? Math.round(batches.reduce((s, b) => s + (b.average_fit_score || 0), 0) / batches.length)
      : 0;

  return (
    <section className="space-y-6" data-testid="recruiter-dashboard-page">
      {/* Hero */}
      <div className="premium-card border-none px-6 py-6" data-testid="recruiter-dashboard-hero">
        <p className="text-sm text-slate-500">Welcome back</p>
        <h2 className="text-4xl sm:text-5xl lg:text-6xl">
          {recruiter?.name || "Recruiter"}'s Home Dashboard
        </h2>
        <p className="mt-3 text-base text-slate-600">
          Company:{" "}
          <span className="font-semibold text-slate-900">{recruiter?.company || "N/A"}</span> · Role:{" "}
          <span className="font-semibold text-slate-900">{recruiter?.role || "N/A"}</span>
        </p>
      </div>

      {/* Quick action cards */}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Link to="/upload" className="premium-card border-none p-5">
          <FileUp size={20} className="text-[#eb6a45]" />
          <p className="mt-4 text-lg font-semibold text-slate-900">Start New Screening</p>
          <p className="text-sm text-slate-500">Upload JD + resume ZIP and run smart ranking.</p>
        </Link>

        <Link to={batchId ? `/results/${batchId}` : "/upload"} className="premium-card border-none p-5">
          <Users size={20} className="text-[#eb6a45]" />
          <p className="mt-4 text-lg font-semibold text-slate-900">Latest Candidate Results</p>
          <p className="text-sm text-slate-500">Open ranked shortlist and candidate insights.</p>
        </Link>

        <Link to={batchId ? `/analytics/${batchId}` : "/upload"} className="premium-card border-none p-5">
          <BellRing size={20} className="text-[#eb6a45]" />
          <p className="mt-4 text-lg font-semibold text-slate-900">Analytics Snapshot</p>
          <p className="text-sm text-slate-500">View score distribution and skill coverage charts.</p>
        </Link>

        <div className="premium-card border-none p-5">
          <ShieldCheck size={20} className="text-[#eb6a45]" />
          <p className="mt-4 text-lg font-semibold text-slate-900">Recruiter Access Secure</p>
          <p className="text-sm text-slate-500">Your JWT session is active with remembered login persistence.</p>
        </div>
      </div>

      {/* Overall stats */}
      {batches.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="premium-card border-none flex items-center gap-4 p-5">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-orange-100">
              <Layers size={20} className="text-[#eb6a45]" />
            </div>
            <div>
              <p className="text-2xl font-bold text-slate-900">{batches.length}</p>
              <p className="text-sm text-slate-500">Total Screening Runs</p>
            </div>
          </div>
          <div className="premium-card border-none flex items-center gap-4 p-5">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-blue-100">
              <TrendingUp size={20} className="text-blue-600" />
            </div>
            <div>
              <p className="text-2xl font-bold text-slate-900">{totalResumes}</p>
              <p className="text-sm text-slate-500">Resumes Processed</p>
            </div>
          </div>
          <div className="premium-card border-none flex items-center gap-4 p-5">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-emerald-100">
              <Sparkles size={20} className="text-emerald-600" />
            </div>
            <div>
              <p className="text-2xl font-bold text-slate-900">{avgFit}%</p>
              <p className="text-sm text-slate-500">Overall Avg Fit Score</p>
            </div>
          </div>
        </div>
      )}

      {/* Screening history as project cards */}
      <div>
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h3 className="text-xl font-semibold text-slate-900">Screening Projects</h3>
            <p className="text-sm text-slate-500">All previous resume screening runs — click any card to explore results.</p>
          </div>
          <Link
            to="/upload"
            className="inline-flex items-center gap-1.5 rounded-full bg-[#eb6a45] px-4 py-2 text-sm font-semibold text-white hover:bg-[#d45e3a]"
          >
            <FileUp size={14} /> New Screening
          </Link>
        </div>

        {loading ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {[1, 2, 3].map((i) => (
              <div key={i} className="premium-card h-64 animate-pulse border-none bg-slate-50" />
            ))}
          </div>
        ) : batches.length === 0 ? (
          <div className="premium-card border-none flex flex-col items-center gap-4 py-16 text-center">
            <div className="flex h-16 w-16 items-center justify-center rounded-full bg-slate-100">
              <Briefcase size={28} className="text-slate-400" />
            </div>
            <div>
              <p className="text-base font-semibold text-slate-700">No screenings yet</p>
              <p className="mt-1 text-sm text-slate-400">
                Run your first resume screening to see it appear here as a project card.
              </p>
            </div>
            <Link
              to="/upload"
              className="inline-flex items-center gap-2 rounded-full bg-[#eb6a45] px-5 py-2.5 text-sm font-semibold text-white hover:bg-[#d45e3a]"
            >
              <FileUp size={15} /> Start First Screening
            </Link>
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {batches.map((batch) => (
              <BatchProjectCard key={batch.batch_id} batch={batch} />
            ))}
            {/* New screening CTA card */}
            <Link
              to="/upload"
              className="premium-card group flex flex-col items-center justify-center gap-3 border-2 border-dashed border-slate-200 bg-transparent p-5 text-center hover:border-[#eb6a45]"
            >
              <div className="flex h-12 w-12 items-center justify-center rounded-full border-2 border-dashed border-slate-300 group-hover:border-[#eb6a45]">
                <FileUp size={20} className="text-slate-400 group-hover:text-[#eb6a45]" />
              </div>
              <div>
                <p className="text-sm font-semibold text-slate-600 group-hover:text-[#eb6a45]">
                  Start New Screening
                </p>
                <p className="text-xs text-slate-400">Upload JD + resumes ZIP</p>
              </div>
              <ChevronRight size={16} className="text-slate-300 group-hover:text-[#eb6a45]" />
            </Link>
          </div>
        )}
      </div>
    </section>
  );
}
