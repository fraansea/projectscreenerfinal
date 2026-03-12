import { useMemo, useState } from "react";
import { motion } from "framer-motion";
import { ArrowRight, FileText, Sparkles } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { toast } from "../components/ui/sonner";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Textarea } from "../components/ui/textarea";
import { Input } from "../components/ui/input";
import { UploadZone } from "../components/UploadZone";
import { startResumeAnalysis } from "../services/api";
import { useAuth } from "../context/AuthContext";

export default function UploadPage() {
  const navigate = useNavigate();
  const { token } = useAuth();
  const [jdText, setJdText] = useState("");
  const [jdFile, setJdFile] = useState(null);
  const [zipFile, setZipFile] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const jdWordCount = useMemo(
    () => jdText.trim().split(/\s+/).filter(Boolean).length,
    [jdText],
  );

  const submitForScreening = async (event) => {
    event.preventDefault();
    if (!zipFile) {
      toast.error("Please upload a ZIP file with resumes.");
      return;
    }

    if (!jdText.trim() && !jdFile) {
      toast.error("Add job description text or upload JD file.");
      return;
    }

    setIsSubmitting(true);
    try {
      const response = await startResumeAnalysis({ jdText, jdFile, zipFile, token });
      localStorage.setItem("last_resume_batch_id", response.batch_id);
      toast.success("Analysis queued successfully.");
      navigate(`/processing/${response.batch_id}`, {
        state: { logs: response.processing_logs || [] },
      });
    } catch (error) {
      const message = error?.response?.data?.detail || "Unable to process files.";
      toast.error(message);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <section className="space-y-6" data-testid="upload-page">
      <motion.div
        initial={{ opacity: 0, y: 15 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35 }}
        className="premium-card border-none px-5 py-5 md:px-7"
        data-testid="upload-top-strip"
      >
        <div className="grid gap-4 lg:grid-cols-[1.1fr_2fr]">
          <div className="flex items-center gap-3" data-testid="upload-date-cta-panel">
            <div className="grid h-16 w-16 place-items-center rounded-full border border-slate-300 bg-white text-2xl font-semibold text-slate-900" data-testid="upload-date-bubble">19</div>
            <div>
              <p className="text-sm text-slate-500" data-testid="upload-date-label">Batch Workflow</p>
              <p className="text-xl font-semibold text-slate-900" data-testid="upload-date-title">Screen new candidate set</p>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3" data-testid="upload-help-cta-row">
            <Button type="button" className="rounded-full bg-[#eb6a45] px-6 text-white hover:bg-[#d7552f]" data-testid="show-tasks-button">
              Show my Tasks <ArrowRight size={16} />
            </Button>
            <p className="text-2xl font-semibold text-slate-900" data-testid="upload-help-title">
              Hey recruiter, need help? <span className="text-slate-400">Just ask me anything.</span>
            </p>
          </div>
        </div>
      </motion.div>

      <motion.div
        initial={{ opacity: 0, y: 15 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, delay: 0.05 }}
        className="grid gap-6 xl:grid-cols-[1fr_1.35fr]"
        data-testid="upload-page-grid"
      >
        <Card className="premium-card border-none" data-testid="upload-intro-card">
          <CardHeader>
            <CardTitle className="text-4xl sm:text-5xl lg:text-6xl" data-testid="upload-main-heading">
              Smart Resume Intake
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 text-sm text-slate-600 md:text-base">
            <p data-testid="upload-main-description">
              Upload your JD and candidate ZIP. We score relevance, verify portfolio evidence,
              and prepare recruiter-grade ranking with analytics.
            </p>
            <div className="premium-soft p-4" data-testid="upload-benefits-box">
              <p className="flex items-center gap-2 font-semibold text-slate-900" data-testid="upload-benefits-title">
                <Sparkles size={16} className="text-[#eb6a45]" /> Verification pipeline
              </p>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-600" data-testid="upload-benefits-list">
                <li data-testid="benefit-ranking">NLP fit scoring and shortlist tiers</li>
                <li data-testid="benefit-skills">Skill gaps and recruiter insights</li>
                <li data-testid="benefit-links">GitHub + LinkedIn proof-based portfolio checks</li>
              </ul>
            </div>
          </CardContent>
        </Card>

        <Card className="premium-card border-none" data-testid="upload-form-card">
          <CardHeader>
            <CardTitle className="text-xl md:text-2xl" data-testid="upload-form-title">
              Upload Module
            </CardTitle>
          </CardHeader>
          <CardContent>
            <form className="space-y-6" onSubmit={submitForScreening} data-testid="upload-form">
              <div className="space-y-2">
                <label className="text-sm font-semibold text-slate-700" data-testid="jd-text-label">
                  Job Description (paste text)
                </label>
                <Textarea
                  value={jdText}
                  onChange={(event) => setJdText(event.target.value)}
                  placeholder="Paste the JD here. Include required skills, experience years, and education criteria."
                  className="min-h-[170px] rounded-2xl border-slate-300 bg-white"
                  data-testid="jd-textarea"
                />
                <p className="text-xs text-slate-500" data-testid="jd-word-count">
                  JD word count: {jdWordCount}
                </p>
              </div>

              <div className="space-y-2">
                <label className="text-sm font-semibold text-slate-700" data-testid="jd-file-label">
                  Or upload JD file (.txt/.pdf/.docx)
                </label>
                <Input
                  type="file"
                  accept=".txt,.pdf,.docx"
                  onChange={(event) => setJdFile(event.target.files?.[0] || null)}
                  className="rounded-full border-slate-300 bg-white"
                  data-testid="jd-file-input"
                />
                <p className="font-mono text-xs text-slate-500" data-testid="jd-file-name">
                  {jdFile ? `Selected: ${jdFile.name}` : "No JD file selected"}
                </p>
              </div>

              <UploadZone file={zipFile} onFileSelect={setZipFile} />

              <Button
                type="submit"
                disabled={isSubmitting}
                className="h-11 w-full rounded-full bg-[#eb6a45] text-white hover:bg-[#d7552f]"
                data-testid="start-screening-button"
              >
                {isSubmitting ? "Queueing batch..." : "Start Resume Screening"}
                <ArrowRight size={16} />
              </Button>
            </form>

            <div className="premium-soft mt-6 p-4 text-sm text-slate-600" data-testid="upload-compatibility-note">
              <p className="flex items-center gap-2 font-semibold text-slate-900" data-testid="upload-compatibility-title">
                <FileText size={16} className="text-[#eb6a45]" /> Supported formats
              </p>
              <p className="mt-2" data-testid="upload-compatibility-description">
                Resume ZIP with PDF/DOCX/TXT + JD via text or document upload.
              </p>
            </div>
          </CardContent>
        </Card>
      </motion.div>
    </section>
  );
}
