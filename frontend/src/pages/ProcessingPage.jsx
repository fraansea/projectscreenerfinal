import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { LoaderCircle } from "lucide-react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { Progress } from "../components/ui/progress";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { toast } from "../components/ui/sonner";
import { getBatchStatus } from "../services/api";
import { useAuth } from "../context/AuthContext";

/* ── Animated AI Processing Visualization ─────────────────────────────────── */
const NODES = [
  { x: 50, y: 50, label: "NLP" },
  { x: 22, y: 28, label: "PDF" },
  { x: 78, y: 28, label: "JD" },
  { x: 18, y: 65, label: "Skills" },
  { x: 82, y: 65, label: "Score" },
  { x: 50, y: 82, label: "Rank" },
];

const EDGES = [
  [0, 1], [0, 2], [0, 3], [0, 4], [0, 5],
  [1, 3], [2, 4], [3, 5], [4, 5],
];

function ProcessingAnimation({ progress }) {
  return (
    <div className="absolute inset-0 overflow-hidden bg-slate-950">
      {/* Subtle grid */}
      <div
        className="absolute inset-0 opacity-[0.07]"
        style={{
          backgroundImage:
            "linear-gradient(#eb6a45 1px,transparent 1px),linear-gradient(90deg,#eb6a45 1px,transparent 1px)",
          backgroundSize: "40px 40px",
        }}
      />

      {/* Radial glow */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_60%_55%_at_50%_50%,rgba(235,106,69,0.18),transparent)]" />

      {/* SVG graph */}
      <svg className="absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">
        {/* Edges */}
        {EDGES.map(([a, b], i) => (
          <motion.line
            key={i}
            x1={NODES[a].x} y1={NODES[a].y}
            x2={NODES[b].x} y2={NODES[b].y}
            stroke="#eb6a45"
            strokeWidth="0.35"
            strokeOpacity="0"
            animate={{ strokeOpacity: [0, 0.5, 0.15, 0.5] }}
            transition={{ duration: 2.5, delay: i * 0.3, repeat: Infinity, ease: "easeInOut" }}
          />
        ))}

        {/* Travelling dot along edges */}
        {EDGES.map(([a, b], i) => (
          <motion.circle
            key={`dot-${i}`}
            r="0.9"
            fill="#f97316"
            initial={{ cx: NODES[a].x, cy: NODES[a].y, opacity: 0 }}
            animate={{
              cx: [NODES[a].x, NODES[b].x, NODES[a].x],
              cy: [NODES[a].y, NODES[b].y, NODES[a].y],
              opacity: [0, 1, 0],
            }}
            transition={{
              duration: 2.2,
              delay: i * 0.55 + 0.4,
              repeat: Infinity,
              ease: "easeInOut",
            }}
          />
        ))}

        {/* Nodes */}
        {NODES.map((n, i) => (
          <g key={i}>
            {/* Pulse ring */}
            <motion.circle
              cx={n.x} cy={n.y} r="4"
              fill="none" stroke="#eb6a45"
              strokeWidth="0.4"
              animate={{ r: [3.5, 6, 3.5], opacity: [0.6, 0, 0.6] }}
              transition={{ duration: 2, delay: i * 0.35, repeat: Infinity }}
            />
            {/* Core */}
            <motion.circle
              cx={n.x} cy={n.y} r="2.8"
              fill="#1e293b" stroke="#eb6a45" strokeWidth="0.6"
              animate={{ opacity: [0.85, 1, 0.85] }}
              transition={{ duration: 1.8, delay: i * 0.2, repeat: Infinity }}
            />
            {/* Label */}
            <text
              x={n.x} y={n.y + 0.85}
              textAnchor="middle"
              fontSize="2.2"
              fill="#fdba74"
              fontFamily="monospace"
              fontWeight="bold"
            >
              {n.label}
            </text>
          </g>
        ))}
      </svg>

      {/* Horizontal scan line */}
      <motion.div
        className="pointer-events-none absolute left-0 right-0 h-px bg-gradient-to-r from-transparent via-orange-400 to-transparent opacity-60"
        animate={{ top: ["10%", "90%", "10%"] }}
        transition={{ duration: 3.5, repeat: Infinity, ease: "easeInOut" }}
      />

      {/* Progress arc label */}
      <div className="absolute bottom-5 left-5 right-5 text-white">
        <motion.p
          className="text-[10px] uppercase tracking-[0.22em] text-orange-300"
          animate={{ opacity: [0.6, 1, 0.6] }}
          transition={{ duration: 1.5, repeat: Infinity }}
        >
          ● Live Processing
        </motion.p>
        <p className="mt-1.5 text-xl font-semibold leading-snug">
          Running NLP extraction<br />and scoring pipeline
        </p>
        {/* Mini progress bar */}
        <div className="mt-3 h-1 w-full overflow-hidden rounded-full bg-white/10">
          <motion.div
            className="h-full rounded-full bg-orange-400"
            animate={{ width: `${progress}%` }}
            transition={{ duration: 0.6 }}
          />
        </div>
        <p className="mt-1 font-mono text-xs text-orange-200">{progress}% complete</p>
      </div>
    </div>
  );
}

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
          <div className="relative min-h-[320px] border-b border-slate-200 lg:border-b-0 lg:border-r" data-testid="processing-visual-panel">
            {isFailed ? (
              /* Failed state — simple dark panel */
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-slate-950">
                <motion.div
                  animate={{ scale: [1, 1.08, 1] }}
                  transition={{ duration: 1.5, repeat: Infinity }}
                  className="flex h-16 w-16 items-center justify-center rounded-full bg-red-500/20 text-4xl"
                >
                  ✕
                </motion.div>
                <p className="text-base font-semibold text-red-400">Processing failed</p>
                <p className="text-xs text-slate-400">Redirecting to upload…</p>
              </div>
            ) : (
              <ProcessingAnimation progress={progress} />
            )}
          </div>

          <div className="p-6 md:p-8" data-testid="processing-content-panel">
            <CardHeader className="p-0">
              <CardTitle className="flex items-center gap-2 text-2xl" data-testid="processing-card-title">
                {isFailed ? (
                  <span className="text-red-500">✕</span>
                ) : (
                  <LoaderCircle size={22} className="animate-spin text-[#eb6a45]" />
                )}
                {isFailed ? "Processing Failed" : "Processing Batch"}
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
