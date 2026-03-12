import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { LoaderCircle } from "lucide-react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { Progress } from "../components/ui/progress";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { toast } from "../components/ui/sonner";
import { getBatchStatus } from "../services/api";
import { useAuth } from "../context/AuthContext";

const abstractImage =
  "https://images.unsplash.com/photo-1581093458791-9f3c3900df4b?crop=entropy&cs=srgb&fm=jpg&q=85";

export default function ProcessingPage() {
  const { batchId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const { token } = useAuth();
  const [progress, setProgress] = useState(15);
  const [statusLabel, setStatusLabel] = useState("Processing");
  const [isFailed, setIsFailed] = useState(false);

  const [liveLogs, setLiveLogs] = useState([]);

  const logs = useMemo(() => {
    if (liveLogs.length) {
      return liveLogs;
    }
    if (location.state?.logs?.length) {
      return location.state.logs;
    }
    return [
      "Preparing files",
      "Extracting resume text",
      "Scoring candidates",
      "Generating analytics",
    ];
  }, [location.state, liveLogs]);

  useEffect(() => {
    let pollingHandle;
    let cancelled = false;

    const pollStatus = async () => {
      if (!batchId || cancelled) return;
      try {
        const status = await getBatchStatus(batchId, token);
        if (cancelled) return;

        setProgress(Math.max(8, Math.min(100, status.progress || 0)));
        setStatusLabel(status.status || "processing");
        setLiveLogs(status.processing_logs || []);

        if (status.status === "completed" || status.completed) {
          setProgress(100);
          navigate(`/results/${batchId}`);
          return;
        }

        if (status.status === "failed") {
          setIsFailed(true);
          toast.error(status.error_message || "Processing failed. Please retry.");
          return;
        }
      } catch (error) {
        const message = error?.response?.data?.detail || "Unable to fetch processing status.";
        toast.error(message);
        setIsFailed(true);
      }
    };

    pollStatus();
    pollingHandle = setInterval(pollStatus, 2500);

    return () => {
      cancelled = true;
      if (pollingHandle) {
        clearInterval(pollingHandle);
      }
    };
  }, [batchId, navigate, token]);

  useEffect(() => {
    if (!isFailed) return;
    const timer = setTimeout(() => navigate("/upload"), 2500);
    return () => clearTimeout(timer);
  }, [isFailed, navigate]);

  return (
    <section className="space-y-8" data-testid="processing-page">
      <Card className="premium-card overflow-hidden border-none" data-testid="processing-main-card">
        <div className="grid gap-0 lg:grid-cols-[1fr_1.1fr]" data-testid="processing-layout-grid">
          <div className="relative min-h-[320px] border-b border-slate-200 lg:border-b-0 lg:border-r">
            <img
              src={abstractImage}
              alt="Processing background"
              className="h-full w-full object-cover object-center"
              data-testid="processing-background-image"
            />
            <div className="absolute inset-0 bg-gradient-to-br from-slate-950/65 via-slate-950/25 to-transparent" />
            <div className="absolute bottom-5 left-5 right-5 text-white">
              <p className="text-xs uppercase tracking-[0.2em] text-orange-200" data-testid="processing-status-tag">
                Live Processing
              </p>
              <h2 className="mt-2 text-2xl font-semibold" data-testid="processing-status-title">
                {isFailed
                  ? "Processing failed"
                  : "Running NLP extraction and scoring pipeline"}
              </h2>
            </div>
          </div>

          <div className="p-6 md:p-8" data-testid="processing-content-panel">
            <CardHeader className="p-0">
              <CardTitle className="flex items-center gap-2 text-2xl" data-testid="processing-card-title">
                <LoaderCircle size={22} className="animate-spin text-[#eb6a45]" />
                Processing Batch
              </CardTitle>
            </CardHeader>

            <CardContent className="mt-5 space-y-6 p-0">
              <div className="space-y-2">
                <p className="text-sm text-slate-600" data-testid="processing-batch-id">
                  Batch ID: <span className="font-mono text-xs text-slate-900">{batchId}</span>
                </p>
                <Progress value={progress} className="h-3 bg-orange-100" data-testid="processing-progress-bar" />
                <p className="score-count text-sm font-semibold text-[#d7552f]" data-testid="processing-progress-text">
                  {isFailed ? "Failed" : `${progress}% complete`} ({statusLabel})
                </p>
              </div>

              <div className="premium-soft space-y-3 p-4" data-testid="processing-log-panel">
                <p className="text-sm font-semibold text-slate-900" data-testid="processing-log-title">
                  Processing logs
                </p>

                <div className="space-y-2" data-testid="processing-log-list">
                  {logs.map((log, index) => (
                    <motion.p
                      key={`${log}-${index}`}
                      initial={{ opacity: 0, x: -10 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: 0.15 * index, duration: 0.25 }}
                      className="text-sm text-slate-600"
                      data-testid={`processing-log-item-${index}`}
                    >
                      • {log}
                    </motion.p>
                  ))}
                </div>
              </div>
            </CardContent>
          </div>
        </div>
      </Card>
    </section>
  );
}
