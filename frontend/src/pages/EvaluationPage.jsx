import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "../components/ui/sonner";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { KpiCard } from "../components/KpiCard";
import { getBatchEvaluation, getResults, setCandidateLabel } from "../services/api";
import { useAuth } from "../context/AuthContext";

const LABELS = [
  { value: 3, label: "Strong fit", pill: "bg-emerald-100 text-emerald-700 border-emerald-200" },
  { value: 2, label: "Good fit", pill: "bg-green-100 text-green-700 border-green-200" },
  { value: 1, label: "Partial fit", pill: "bg-amber-100 text-amber-700 border-amber-200" },
  { value: 0, label: "Not relevant", pill: "bg-slate-100 text-slate-600 border-slate-200" },
];

function MetricRow({ name, value, helper }) {
  return (
    <div className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-4 py-3">
      <div>
        <p className="text-sm font-semibold text-slate-900">{name}</p>
        {helper && <p className="text-xs text-slate-500">{helper}</p>}
      </div>
      <p className="font-mono text-sm font-bold text-slate-800">{value}</p>
    </div>
  );
}

export default function EvaluationPage() {
  const { batchId } = useParams();
  const { token } = useAuth();

  const [analysis, setAnalysis] = useState(null);
  const [evalData, setEvalData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [labeling, setLabeling] = useState(false);
  const [localLabels, setLocalLabels] = useState({}); // candidate_id -> label

  useEffect(() => {
    const load = async () => {
      if (!batchId) return;
      setLoading(true);
      try {
        const [results, evaluation] = await Promise.all([
          getResults(batchId, token),
          getBatchEvaluation(batchId, token).catch(() => null),
        ]);
        setAnalysis(results);
        setEvalData(evaluation);
      } catch (e) {
        toast.error(e?.response?.data?.detail || "Unable to load evaluation data");
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [batchId, token]);

  const candidates = useMemo(() => analysis?.results || [], [analysis]);
  const labeledCount = useMemo(() => Object.keys(localLabels).length, [localLabels]);

  const metrics = evalData?.metrics || {};
  const fmt = (v) => (typeof v === "number" ? v.toFixed(3) : "0.000");

  if (loading) {
    return (
      <Card className="premium-card border-none">
        <CardContent className="p-6 text-slate-600">Loading evaluation…</CardContent>
      </Card>
    );
  }

  if (!analysis) {
    return (
      <Card className="premium-card border-none">
        <CardContent className="p-6 text-slate-600">No batch found.</CardContent>
      </Card>
    );
  }

  return (
    <section className="space-y-6" data-testid="evaluation-page">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-4xl sm:text-5xl lg:text-6xl">Ranking Evaluation</h2>
          <p className="mt-2 text-sm text-slate-600">
            Batch: <span className="font-mono text-xs text-slate-900">{batchId}</span>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link to={`/results/${batchId}`}>
            <Button type="button" variant="outline" className="rounded-full border-slate-300 bg-white">
              Back to Results
            </Button>
          </Link>
          <Button
            type="button"
            className="rounded-full bg-[#eb6a45] text-white hover:bg-[#d7552f]"
            onClick={async () => {
              if (!batchId) return;
              try {
                const fresh = await getBatchEvaluation(batchId, token);
                setEvalData(fresh);
                toast.success("Evaluation refreshed");
              } catch (e) {
                toast.error(e?.response?.data?.detail || "Evaluation refresh failed");
              }
            }}
          >
            Refresh Metrics
          </Button>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <KpiCard title="Candidates" value={candidates.length} helper="In ranking list" />
        <KpiCard title="Labeled (this session)" value={labeledCount} helper="Submitted labels" />
        <KpiCard title="nDCG@10" value={fmt(metrics["ndcg@10"])} helper="Graded relevance (0..3)" />
        <KpiCard title="Precision@5" value={fmt(metrics["precision@5"])} helper="Relevant label≥2 in top 5" />
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <Card className="premium-card border-none">
          <CardHeader>
            <CardTitle className="text-xl">Batch Metrics</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <MetricRow name="Precision@10" value={fmt(metrics["precision@10"])} helper="Relevant label≥2 in top 10" />
            <MetricRow name="Recall@10" value={fmt(metrics["recall@10"])} helper="Coverage of all relevant labeled candidates" />
            <MetricRow name="MAP" value={fmt(metrics["map"])} helper="Average precision over ranking" />
            <MetricRow name="MRR" value={fmt(metrics["mrr"])} helper="Reciprocal rank of first relevant" />
            <MetricRow name="Agreement" value={fmt(metrics["agreement"])} helper="Simple consistency indicator" />
            {evalData?.note && <p className="text-xs text-slate-400">{evalData.note}</p>}
          </CardContent>
        </Card>

        <Card className="premium-card border-none">
          <CardHeader>
            <CardTitle className="text-xl">Label Candidates (0–3)</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm text-slate-600">
              Add recruiter outcome labels so we can measure ranking quality. Labels are stored per batch and power metrics like nDCG.
            </p>
            <div className="rounded-xl border border-slate-200 bg-white">
              <div className="grid grid-cols-[2fr_1fr] gap-2 border-b border-slate-100 px-4 py-2 text-xs font-semibold text-slate-500">
                <span>Candidate</span>
                <span>Label</span>
              </div>
              <div className="max-h-[360px] overflow-auto">
                {candidates.map((c) => (
                  <div key={c.candidate_id} className="grid grid-cols-[2fr_1fr] items-center gap-2 border-b border-slate-50 px-4 py-2">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold text-slate-900">{c.candidate_name}</p>
                      <p className="truncate font-mono text-[11px] text-slate-400">{c.source_file}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <select
                        className="h-9 w-full rounded-full border border-slate-300 bg-white px-3 text-sm"
                        value={localLabels[c.candidate_id] ?? ""}
                        onChange={(e) => {
                          const v = e.target.value;
                          setLocalLabels((prev) => ({ ...prev, [c.candidate_id]: v === "" ? undefined : Number(v) }));
                        }}
                        disabled={labeling}
                      >
                        <option value="">—</option>
                        {LABELS.map((l) => (
                          <option key={l.value} value={l.value}>
                            {l.value} · {l.label}
                          </option>
                        ))}
                      </select>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="rounded-full"
                        disabled={labeling || localLabels[c.candidate_id] === undefined || localLabels[c.candidate_id] === ""}
                        onClick={async () => {
                          const label = localLabels[c.candidate_id];
                          if (label === undefined || label === "") return;
                          setLabeling(true);
                          try {
                            await setCandidateLabel({
                              batchId,
                              candidateId: c.candidate_id,
                              label: Number(label),
                              token,
                            });
                            toast.success(`Saved label ${label} for ${c.candidate_name}`);
                          } catch (e) {
                            toast.error(e?.response?.data?.detail || "Failed to save label");
                          } finally {
                            setLabeling(false);
                          }
                        }}
                      >
                        Save
                      </Button>
                    </div>
                  </div>
                ))}
                {!candidates.length && (
                  <div className="p-4 text-sm text-slate-500">No candidates in this batch.</div>
                )}
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </section>
  );
}

