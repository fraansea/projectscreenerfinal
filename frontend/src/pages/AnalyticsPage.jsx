import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { toast } from "../components/ui/sonner";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { KpiCard } from "../components/KpiCard";
import { getHeatmap, getResults } from "../services/api";
import { useAuth } from "../context/AuthContext";

const tierPieColors = ["#eb6a45", "#f39a6b", "#d94833"];

export default function AnalyticsPage() {
  const { batchId: routeBatchId } = useParams();
  const batchId =
    routeBatchId === "latest" ? localStorage.getItem("last_resume_batch_id") : routeBatchId;
  const { token } = useAuth();

  const [analysis, setAnalysis] = useState(null);
  const [loading, setLoading] = useState(true);
  const [heatmap, setHeatmap] = useState(null);

  useEffect(() => {
    const load = async () => {
      if (!batchId) {
        setLoading(false);
        return;
      }

      const cached = sessionStorage.getItem(`analysis_${batchId}`);
      if (cached) {
        setAnalysis(JSON.parse(cached));
        setLoading(false);
        return;
      }

      try {
        const data = await getResults(batchId, token);
        setAnalysis(data);
        sessionStorage.setItem(`analysis_${batchId}`, JSON.stringify(data));
        // Load heatmap data
        try {
          const hmData = await getHeatmap(batchId, token);
          setHeatmap(hmData);
        } catch (_) {}
      } catch (error) {
        toast.error(error?.response?.data?.detail || "Unable to load analytics");
      } finally {
        setLoading(false);
      }
    };

    load();
  }, [batchId, token]);

  const tierDistribution = useMemo(() => {
    const scores = analysis?.analytics?.score_distribution;
    if (!scores) return [];
    return [
      { name: "Top Tier", value: scores.top || 0 },
      { name: "Middle Tier", value: scores.middle || 0 },
      { name: "Low Tier", value: scores.low || 0 },
    ];
  }, [analysis]);

  const atsStats = useMemo(() => {
    if (!analysis?.results?.length) return { green: 0, orange: 0, red: 0, avg: 0 };
    const results = analysis.results;
    const green = results.filter((r) => r.ats_score?.label === "Green").length;
    const orange = results.filter((r) => r.ats_score?.label === "Orange").length;
    const red = results.filter((r) => r.ats_score?.label === "Red").length;
    const avg = Math.round(results.reduce((s, r) => s + (r.ats_score?.score ?? 100), 0) / results.length);
    return { green, orange, red, avg };
  }, [analysis]);

  const trustStats = useMemo(() => {
    if (!analysis?.results?.length) return { high: 0, medium: 0, low: 0, avg: 0 };
    const results = analysis.results;
    const high = results.filter((r) => r.trust_score?.label === "High").length;
    const medium = results.filter((r) => r.trust_score?.label === "Medium").length;
    const low = results.filter((r) => r.trust_score?.label === "Low").length;
    const avg = Math.round(results.reduce((s, r) => s + (r.trust_score?.score ?? 100), 0) / results.length);
    return { high, medium, low, avg };
  }, [analysis]);

  const biasCount = useMemo(() => {
    if (!analysis?.results?.length) return 0;
    return analysis.results.filter((r) => r.bias_flags?.gender_skew_detected || r.bias_flags?.university_bias_detected).length;
  }, [analysis]);

  const careerData = useMemo(() => {
    if (!analysis?.results?.length) return [];
    return analysis.results.slice(0, 10).map((r) => ({
      name: r.candidate_name.split(" ")[0],
      career: r.career_trajectory?.score ?? 0,
      trust: r.trust_score?.score ?? 0,
      ats: r.ats_score?.score ?? 100,
    }));
  }, [analysis]);

  if (!batchId) {
    return (
      <Card className="premium-card border-none" data-testid="analytics-empty-batch-card">
        <CardContent className="p-6 text-slate-600" data-testid="analytics-empty-batch-text">
          No analytics available yet. Run one screening batch first.
        </CardContent>
      </Card>
    );
  }

  if (loading) {
    return (
      <Card className="premium-card border-none" data-testid="analytics-loading-card">
        <CardContent className="p-6 text-slate-600" data-testid="analytics-loading-text">
          Loading analytics dashboard...
        </CardContent>
      </Card>
    );
  }

  if (!analysis) {
    return (
      <Card className="premium-card border-none" data-testid="analytics-no-data-card">
        <CardContent className="p-6 text-slate-600" data-testid="analytics-no-data-text">
          We could not find this analytics dataset.
        </CardContent>
      </Card>
    );
  }

  return (
    <section className="space-y-6" data-testid="analytics-page">
      <div className="premium-card border-none px-5 py-5 md:px-6" data-testid="analytics-hero-strip">
        <div className="grid gap-3 lg:grid-cols-[1.4fr_1fr]" data-testid="analytics-hero-grid">
          <p className="text-2xl font-semibold text-slate-900" data-testid="analytics-hero-title">
            Evidence-first analytics for smarter recruiter decisions.
          </p>
          <p className="text-sm text-slate-500 md:text-base" data-testid="analytics-hero-description">
            Track shortlist quality, score distribution, and JD skill coverage with a premium dashboard view.
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-start justify-between gap-3" data-testid="analytics-header-row">
        <div>
          <h2 className="text-4xl sm:text-5xl lg:text-6xl" data-testid="analytics-main-heading">
            Screening Analytics
          </h2>
          <p className="mt-2 text-sm text-slate-600 md:text-base" data-testid="analytics-subheading">
            Visual overview for HR decision making and shortlist quality.
          </p>
        </div>
        <Link to={`/results/${batchId}`} data-testid="back-to-results-link">
          <Button className="rounded-full bg-[#eb6a45] text-white hover:bg-[#d7552f]" data-testid="back-to-results-button">
            Back to Results
          </Button>
        </Link>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4" data-testid="analytics-kpi-grid">
        <KpiCard
          title="Resumes Uploaded"
          value={analysis.analytics.resumes_uploaded}
          helper="Total candidates"
          testId="analytics-kpi-resumes"
        />
        <KpiCard
          title="Average Fit Score"
          value={`${analysis.analytics.average_fit_score}%`}
          helper="Overall quality"
          testId="analytics-kpi-average-fit"
        />
        <KpiCard
          title="Candidates > 80%"
          value={analysis.analytics.candidates_above_80}
          helper="Immediate shortlist"
          testId="analytics-kpi-shortlist"
        />
        <KpiCard
          title="JD Keywords"
          value={analysis.jd_keywords.length}
          helper="Extracted reference terms"
          testId="analytics-kpi-keywords"
        />
      </div>

      <div className="grid gap-5 xl:grid-cols-2" data-testid="analytics-chart-grid">
        <Card className="premium-card border-none" data-testid="fit-score-distribution-card">
          <CardHeader>
            <CardTitle className="text-xl" data-testid="fit-score-distribution-title">
              Candidate Fit Score Distribution
            </CardTitle>
          </CardHeader>
          <CardContent className="h-[320px]" data-testid="fit-score-distribution-chart-wrap">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={analysis.analytics.candidate_scores}>
                <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
                <XAxis dataKey="candidate_name" tick={{ fontSize: 11 }} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} />
                <Tooltip />
                <Bar dataKey="fit_score" fill="#eb6a45" radius={[8, 8, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card className="premium-card border-none" data-testid="tier-pie-card">
          <CardHeader>
            <CardTitle className="text-xl" data-testid="tier-pie-title">
              Tier Coverage Breakdown
            </CardTitle>
          </CardHeader>
          <CardContent className="h-[320px]" data-testid="tier-pie-chart-wrap">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie data={tierDistribution} dataKey="value" nameKey="name" innerRadius={56} outerRadius={96}>
                  {tierDistribution.map((entry, index) => (
                    <Cell key={entry.name} fill={tierPieColors[index % tierPieColors.length]} />
                  ))}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </div>

      <Card className="premium-card border-none" data-testid="skill-coverage-card">
        <CardHeader>
          <CardTitle className="text-xl" data-testid="skill-coverage-title">
            Skill Coverage vs JD Requirements
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" data-testid="skill-coverage-list">
          {analysis.analytics.skill_coverage.map((item, index) => (
            <div key={`${item.skill}-${index}`} className="premium-soft p-3">
              <p className="font-mono text-xs text-slate-600">{item.skill}</p>
              <p className="mt-1 text-lg font-semibold text-slate-900">{item.matched_count} candidate(s)</p>
            </div>
          ))}
        </CardContent>
      </Card>

      {/* ── NEW: ATS + Trust + Bias KPIs ── */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4" data-testid="advanced-kpi-grid">
        <KpiCard title="Avg ATS Score" value={`${atsStats.avg}%`} helper={`✅ ${atsStats.green} green · ⚠️ ${atsStats.orange} orange · ❌ ${atsStats.red} red`} testId="kpi-ats-avg" />
        <KpiCard title="Avg Trust Score" value={`${trustStats.avg}%`} helper={`🟢 ${trustStats.high} high · 🟡 ${trustStats.medium} med · 🔴 ${trustStats.low} low`} testId="kpi-trust-avg" />
        <KpiCard title="Bias Signals" value={biasCount} helper="Candidates with potential bias flags" testId="kpi-bias-count" />
        <KpiCard title="LLM Enhanced" value={analysis.llm_tech_stack_enhanced ? "Yes" : "No"} helper="Groq Llama 3.1 70B tech stack extraction" testId="kpi-llm-enhanced" />
      </div>

      {/* ── NEW: Career / Trust / ATS multi-bar chart ── */}
      {careerData.length > 0 && (
        <Card className="premium-card border-none" data-testid="career-trust-chart-card">
          <CardHeader>
            <CardTitle className="text-xl">Career · Trust · ATS Scores per Candidate</CardTitle>
          </CardHeader>
          <CardContent className="h-[320px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={careerData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
                <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} />
                <Tooltip />
                <Bar dataKey="career" name="Career" fill="#8b5cf6" radius={[4, 4, 0, 0]} />
                <Bar dataKey="trust" name="Trust" fill="#10b981" radius={[4, 4, 0, 0]} />
                <Bar dataKey="ats" name="ATS" fill="#eb6a45" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      )}

      {/* ── NEW: Skill Heatmap ── */}
      {heatmap && heatmap.skills.length > 0 && (
        <Card className="premium-card border-none" data-testid="skill-heatmap-card">
          <CardHeader>
            <CardTitle className="text-xl">Skill Gap Heatmap — Candidates × JD Skills</CardTitle>
          </CardHeader>
          <CardContent className="soft-scrollbar overflow-auto">
            <table className="min-w-full text-xs">
              <thead>
                <tr>
                  <th className="sticky left-0 bg-white pr-4 text-left text-slate-700 font-semibold py-2">Candidate</th>
                  <th className="pr-3 text-slate-500 font-medium py-2">Fit%</th>
                  {heatmap.skills.map((skill) => (
                    <th key={skill} className="px-2 py-2 font-mono text-slate-600 font-medium whitespace-nowrap">{skill}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {heatmap.candidates.map((row, ri) => (
                  <tr key={ri} className="border-t border-slate-100">
                    <td className="sticky left-0 bg-white pr-4 py-2 font-semibold text-slate-800 whitespace-nowrap">{row.candidate}</td>
                    <td className="pr-3 py-2 text-slate-500">{row.fit_score}%</td>
                    {heatmap.skills.map((skill) => (
                      <td key={skill} className="px-2 py-2 text-center">
                        <span className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-medium ${
                          row[skill] ? "bg-green-100 text-green-700" : "bg-red-50 text-red-400"
                        }`}>
                          {row[skill] ? "✅" : "❌"}
                        </span>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}

      {/* ── NEW: Bias & Fairness summary ── */}
      {analysis.results.some((r) => r.bias_flags?.flags?.length) && (
        <Card className="premium-card border-none" data-testid="bias-monitor-card">
          <CardHeader>
            <CardTitle className="text-xl">Bias & Fairness Monitor</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {analysis.results
              .filter((r) => r.bias_flags?.flags?.length)
              .map((r) => (
                <div key={r.candidate_id} className="rounded-md border border-purple-200 bg-purple-50 p-3">
                  <p className="text-xs font-semibold text-purple-800">{r.candidate_name}</p>
                  {r.bias_flags.flags.map((f, i) => (
                    <p key={i} className="text-xs text-purple-700">{f}</p>
                  ))}
                  <p className="mt-1 text-[11px] italic text-purple-500">{r.bias_flags.diversity_note}</p>
                </div>
              ))}
          </CardContent>
        </Card>
      )}
    </section>
  );
}
