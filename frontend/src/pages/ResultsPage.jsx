import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ChevronDown, ChevronUp, Copy, Download, ExternalLink, Mail, X, Send, Clock, ShieldCheck } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { toast } from "../components/ui/sonner";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Input } from "../components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { KpiCard } from "../components/KpiCard";
import { ScoreBadge } from "../components/ScoreBadge";
import axios from "axios";
import { getResults, sendEmail, getEmailHistory } from "../services/api";
import { useAuth } from "../context/AuthContext";

const TEMPLATE_COLORS = {
  advance: { pill: "border-green-200 bg-green-50 text-green-700", dot: "bg-green-500", label: "🎉 Advance" },
  waitlist: { pill: "border-amber-200 bg-amber-50 text-amber-700", dot: "bg-amber-500", label: "⏳ Waitlist" },
  reject: { pill: "border-blue-200 bg-blue-50 text-blue-700", dot: "bg-blue-500", label: "❌ Reject" },
};

function EmailModal({ candidate, onClose, token }) {
  const email = candidate.email_template || {};
  const [templateType, setTemplateType] = useState(email.template_type || "advance");
  const [to, setTo] = useState(candidate.candidate_email || "");
  const [cc, setCc] = useState("");
  const [bcc, setBcc] = useState("");
  const [subject, setSubject] = useState(email.subject || "");
  const [body, setBody] = useState(email.body || "");
  const [sending, setSending] = useState(false);
  const overlayRef = useRef(null);

  const templates = {
    advance: { subject: email.subject || `Interview Invitation — ${candidate.candidate_name}`, body: email.body || "" },
    waitlist: {
      subject: `Application Update — ${candidate.candidate_name}`,
      body: `Hi ${candidate.candidate_name.split(" ")[0]},\n\nThank you for applying. Your profile was competitive and we've added you to our talent pipeline.\n\nWe'll reach out when a matching position opens.\n\nBest regards,\nHR Team`,
    },
    reject: {
      subject: `Application Status — ${candidate.candidate_name}`,
      body: `Hi ${candidate.candidate_name.split(" ")[0]},\n\nThank you for your interest in this role. After careful review, we won't be moving forward at this time.\n\nWe appreciate the time you invested and encourage you to apply for future roles.\n\nBest regards,\nHR Team`,
    },
  };

  // When the backend already generated the correct template, use it for whichever type matches
  if (email.template_type === "advance") templates.advance = { subject: email.subject, body: email.body };
  if (email.template_type === "waitlist") templates.waitlist = { subject: email.subject, body: email.body };
  if (email.template_type === "reject") templates.reject = { subject: email.subject, body: email.body };

  const switchTemplate = (type) => {
    setTemplateType(type);
    setSubject(templates[type].subject);
    setBody(templates[type].body);
  };

  const handleSend = async () => {
    if (!to.trim()) { toast.error("Recipient email is required."); return; }
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(to.trim())) { toast.error("Invalid email address."); return; }

    setSending(true);
    try {
      const result = await sendEmail(
        { to: to.trim(), subject, body, cc: cc.trim() || undefined, bcc: bcc.trim() || undefined,
          template_type: templateType, candidate_id: candidate.candidate_id, candidate_name: candidate.candidate_name },
        token
      );
      if (result.delivery_mode === "queued") {
        toast.success(`Email queued for ${to} — saved to outbox (SMTP not configured yet).`);
      } else {
        toast.success(`Email sent to ${to}!`);
      }
      onClose(true);
    } catch (err) {
      const detail = err?.response?.data?.detail || "Failed to send email. Check SMTP settings.";
      toast.error(detail);
    } finally {
      setSending(false);
    }
  };

  return (
    <AnimatePresence>
      <motion.div
        ref={overlayRef}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
        onClick={(e) => { if (e.target === overlayRef.current) onClose(false); }}
      >
        <motion.div
          initial={{ opacity: 0, scale: 0.95, y: 20 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.95, y: 20 }}
          transition={{ type: "spring", stiffness: 300, damping: 25 }}
          className="relative w-full max-w-2xl rounded-2xl bg-white shadow-2xl"
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex items-center justify-between border-b border-slate-100 px-6 py-4">
            <div className="flex items-center gap-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-full bg-[#eb6a45]/10">
                <Mail size={16} className="text-[#eb6a45]" />
              </div>
              <div>
                <p className="font-semibold text-slate-900">Send Email</p>
                <p className="text-xs text-slate-500">{candidate.candidate_name}</p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => onClose(false)}
              className="flex h-8 w-8 items-center justify-center rounded-full text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700"
            >
              <X size={16} />
            </button>
          </div>

          <div className="space-y-4 px-6 py-5">
            {/* Template selector */}
            <div>
              <label className="mb-2 block text-xs font-semibold text-slate-700">Template</label>
              <div className="flex gap-2">
                {Object.entries(TEMPLATE_COLORS).map(([type, style]) => (
                  <button
                    key={type}
                    type="button"
                    onClick={() => switchTemplate(type)}
                    className={`flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-semibold transition-all ${
                      templateType === type ? style.pill + " ring-2 ring-offset-1 ring-current" : "border-slate-200 bg-white text-slate-600 hover:border-slate-300"
                    }`}
                  >
                    <span className={`h-2 w-2 rounded-full ${style.dot}`} />
                    {style.label}
                  </button>
                ))}
              </div>
            </div>

            {/* To field */}
            <div>
              <label className="mb-1 block text-xs font-semibold text-slate-700">To</label>
              <Input
                value={to}
                onChange={(e) => setTo(e.target.value)}
                placeholder="candidate@email.com"
                className="rounded-lg border-slate-200 text-sm focus:border-[#eb6a45] focus:ring-[#eb6a45]"
              />
            </div>

            {/* CC / BCC */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-xs font-semibold text-slate-700">CC</label>
                <Input value={cc} onChange={(e) => setCc(e.target.value)} placeholder="cc@email.com" className="rounded-lg border-slate-200 text-sm" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-semibold text-slate-700">BCC</label>
                <Input value={bcc} onChange={(e) => setBcc(e.target.value)} placeholder="bcc@email.com" className="rounded-lg border-slate-200 text-sm" />
              </div>
            </div>

            {/* Subject */}
            <div>
              <label className="mb-1 block text-xs font-semibold text-slate-700">Subject</label>
              <Input
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                className="rounded-lg border-slate-200 text-sm focus:border-[#eb6a45] focus:ring-[#eb6a45]"
              />
            </div>

            {/* Body */}
            <div>
              <label className="mb-1 block text-xs font-semibold text-slate-700">Message</label>
              <textarea
                value={body}
                onChange={(e) => setBody(e.target.value)}
                rows={8}
                className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 font-sans text-sm text-slate-700 leading-relaxed focus:border-[#eb6a45] focus:outline-none focus:ring-1 focus:ring-[#eb6a45]"
              />
            </div>
          </div>

          {/* Footer */}
          <div className="flex items-center justify-between border-t border-slate-100 px-6 py-4">
            <p className="text-xs text-slate-400">Sent via Gmail SMTP · PIXLS Hiring</p>
            <div className="flex gap-2">
              <Button type="button" variant="outline" onClick={() => onClose(false)} className="rounded-full border-slate-200 text-sm">
                Cancel
              </Button>
              <Button
                type="button"
                onClick={handleSend}
                disabled={sending}
                className="rounded-full bg-[#eb6a45] text-white hover:bg-[#d7552f] disabled:opacity-60"
              >
                {sending ? (
                  <span className="flex items-center gap-2"><span className="h-3 w-3 animate-spin rounded-full border-2 border-white/30 border-t-white" /> Sending…</span>
                ) : (
                  <span className="flex items-center gap-2"><Send size={13} /> Send Email</span>
                )}
              </Button>
            </div>
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}

const TierTag = ({ tier, testId }) => {
  const style =
    tier === "Top Tier"
      ? "border-green-200 bg-green-50 text-green-700"
      : tier === "Middle Tier"
        ? "border-amber-200 bg-amber-50 text-amber-700"
        : "border-red-200 bg-red-50 text-red-700";

  return (
    <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${style}`} data-testid={testId}>
      {tier}
    </span>
  );
};

// ── Verification Score Badge & Modal ──────────────────────────────────────────

function VerificationModal({ candidate, onClose }) {
  const vs = candidate.verification_summary || {};
  const score = vs.score ?? 0;
  const checks = vs.checks || [];
  const color =
    vs.badge_color === "green" ? { ring: "ring-green-300", bg: "bg-green-100", text: "text-green-800", bar: "bg-green-500" }
    : vs.badge_color === "yellow" ? { ring: "ring-yellow-300", bg: "bg-yellow-100", text: "text-yellow-800", bar: "bg-yellow-400" }
    : vs.badge_color === "orange" ? { ring: "ring-orange-300", bg: "bg-orange-100", text: "text-orange-800", bar: "bg-orange-500" }
    : { ring: "ring-red-300", bg: "bg-red-100", text: "text-red-700", bar: "bg-red-500" };

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
        onClick={onClose}
      >
        <motion.div
          initial={{ opacity: 0, scale: 0.95, y: 16 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.95, y: 16 }}
          transition={{ type: "spring", stiffness: 300, damping: 26 }}
          className="relative w-full max-w-md rounded-2xl bg-white shadow-2xl"
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex items-center justify-between border-b border-slate-100 px-6 py-4">
            <div className="flex items-center gap-3">
              <div className={`flex h-9 w-9 items-center justify-center rounded-full ${color.bg}`}>
                <ShieldCheck size={16} className={color.text} />
              </div>
              <div>
                <p className="font-semibold text-slate-900">Verification Breakdown</p>
                <p className="text-xs text-slate-500">{candidate.candidate_name}</p>
              </div>
            </div>
            <button type="button" onClick={onClose} className="flex h-8 w-8 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100 hover:text-slate-700">
              <X size={16} />
            </button>
          </div>

          {/* Score ring */}
          <div className="flex flex-col items-center py-6 gap-2">
            <div className={`flex h-20 w-20 items-center justify-center rounded-full ring-4 ${color.ring} ${color.bg}`}>
              <span className={`text-2xl font-extrabold ${color.text}`}>{score}</span>
            </div>
            <p className={`text-sm font-semibold ${color.text}`}>{vs.status || "Review Required"}</p>
            <p className="text-xs text-slate-400">{vs.checks_passed ?? 0}/{vs.checks_total ?? 5} checks passed</p>
            <div className="mt-2 w-48 rounded-full bg-slate-100 h-2">
              <div className={`h-2 rounded-full ${color.bar} transition-all`} style={{ width: `${score}%` }} />
            </div>
          </div>

          {/* Checks list */}
          <div className="px-6 pb-6 space-y-2">
            {checks.map((c, i) => (
              <div key={i} className={`flex items-start gap-2 rounded-lg p-3 text-sm
                ${c.startsWith("✅") ? "bg-green-50 text-green-800"
                  : c.startsWith("⚠️") ? "bg-yellow-50 text-yellow-800"
                  : "bg-red-50 text-red-700"}`}>
                {c}
              </div>
            ))}
            {!checks.length && <p className="text-xs text-slate-400 italic">No check data available.</p>}
          </div>

          {/* Footer */}
          <div className="flex justify-end border-t border-slate-100 px-6 py-3">
            <button type="button" onClick={onClose} className="rounded-full border border-slate-200 px-4 py-1.5 text-sm text-slate-600 hover:border-[#eb6a45] hover:text-[#eb6a45]">
              Close
            </button>
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}

function VerificationBadge({ candidate }) {
  const vs = candidate.verification_summary || {};
  const score = vs.score ?? 0;
  const [showModal, setShowModal] = useState(false);

  const dot =
    vs.badge_color === "green" ? "bg-green-500"
    : vs.badge_color === "yellow" ? "bg-yellow-400"
    : vs.badge_color === "orange" ? "bg-orange-400"
    : "bg-red-400";

  return (
    <>
      <button
        type="button"
        onClick={() => setShowModal(true)}
        className="flex flex-col items-start gap-0.5 rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-left transition-colors hover:border-slate-400"
        title="Click for full verification breakdown"
      >
        <div className="flex items-center gap-1.5">
          <span className={`h-2 w-2 rounded-full shrink-0 ${dot}`} />
          <span className="text-sm font-bold text-slate-800">{score}/100</span>
        </div>
        <span className="text-[10px] text-slate-400">{vs.checks_passed ?? 0}/5 checks</span>
      </button>
      {showModal && (
        <VerificationModal candidate={candidate} onClose={() => setShowModal(false)} />
      )}
    </>
  );
}

const DETAIL_TABS = ["Skills", "ATS", "Trust", "Career", "Notable Achievements", "Email", "Portfolio"];

const ATSBadge = ({ label, score }) => {
  const style = label === "Green"
    ? "border-green-200 bg-green-50 text-green-700"
    : label === "Orange"
      ? "border-amber-200 bg-amber-50 text-amber-700"
      : "border-red-200 bg-red-50 text-red-700";
  return (
    <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${style}`}>
      ATS {score}% · {label}
    </span>
  );
};

function CandidateDetailPanel({ candidate, analysis }) {
  const [activeTab, setActiveTab] = useState("Skills");
  const cid = candidate.candidate_id;
  const ats = candidate.ats_score || {};
  const trust = candidate.trust_score || {};
  const career = candidate.career_trajectory || {};
  const bias = candidate.bias_flags || {};
  const iq = candidate.interview_questions || {};
  const email = candidate.email_template || {};
  const advice = candidate.resume_advice || {};

  const copyToClipboard = (text) => {
    navigator.clipboard?.writeText(text).catch(() => {});
    toast.success("Copied to clipboard");
  };

  return (
    <tr data-testid={`candidate-detail-row-${cid}`}>
      <td colSpan={9} className="bg-slate-50 p-4">
        {/* Tab bar */}
        <div className="mb-4 flex flex-wrap gap-1 border-b border-slate-200 pb-2">
          {DETAIL_TABS.map((tab) => (
            <button
              key={tab}
              type="button"
              onClick={() => setActiveTab(tab)}
              className={`rounded-full px-3 py-1 text-xs font-semibold transition-colors ${
                activeTab === tab
                  ? "bg-[#eb6a45] text-white"
                  : "border border-slate-200 bg-white text-slate-600 hover:border-[#eb6a45] hover:text-[#eb6a45]"
              }`}
            >
              {tab}
            </button>
          ))}
        </div>

        {/* ── Skills tab (original) ── */}
        {activeTab === "Skills" && (
          <div className="grid gap-4 lg:grid-cols-3" data-testid={`candidate-detail-grid-${cid}`}>
            <div>
              <p className="text-sm font-semibold text-slate-900">Matched Skills</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {(candidate.matched_skills.length ? candidate.matched_skills : ["None"]).map((skill) => (
                  <span key={skill} className="rounded-md border border-green-200 bg-green-50 px-2 py-1 font-mono text-xs text-green-700">{skill}</span>
                ))}
              </div>
            </div>
            <div>
              <p className="text-sm font-semibold text-slate-900">Missing Skills</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {(candidate.missing_skills.length ? candidate.missing_skills : ["None"]).map((skill) => (
                  <span key={skill} className="rounded-md border border-red-200 bg-red-50 px-2 py-1 font-mono text-xs text-red-700">{skill}</span>
                ))}
              </div>
            </div>
            <div>
              <p className="text-sm font-semibold text-slate-900">Smart Portfolio Verifier</p>
              <div className="mt-2 rounded-md border border-blue-200 bg-blue-50 p-2">
                <p className="text-xs text-blue-900">
                  Stack Coverage: {candidate.verified_links.github_analysis?.stack_coverage_pct || 0}% | Best Complexity: {candidate.verified_links.github_analysis?.best_project_complexity || 0}/10
                </p>
                <p className="text-xs text-blue-900">{candidate.verified_links.smart_portfolio?.hr_insight || "Portfolio insight unavailable"}</p>
              </div>
              <div className="mt-2 rounded-md border border-slate-200 bg-white p-2">
                <p className="text-xs font-semibold text-slate-700">Extraction Intelligence</p>
                <p className="mt-1 text-xs text-slate-600">
                  GitHub:{" "}
                  {candidate.github_extraction_method === "llm-groq"
                    ? <span className="font-medium text-violet-600">🤖 LLM (Llama 3.1 70B · Groq)</span>
                    : <span className="font-medium text-green-600">✅ Rule-based (pdfplumber)</span>}
                </p>
                <p className="text-xs text-slate-600">
                  Tech Stack:{" "}
                  {analysis.llm_tech_stack_enhanced
                    ? <span className="font-medium text-violet-600">🤖 LLM (Llama 3.1 70B · Groq)</span>
                    : <span className="font-medium text-green-600">✅ Rule-based (skill dictionary)</span>}
                </p>
              </div>
              <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-slate-600">
                {(candidate.suggested_improvements.length ? candidate.suggested_improvements : ["Profile aligned with JD."]).map((item, i) => (
                  <li key={i}>{item}</li>
                ))}
              </ul>
              {candidate.verified_links.github_url && (
                <a href={candidate.verified_links.github_url} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1 text-xs font-semibold text-blue-600 hover:text-blue-800">
                  Open GitHub <ExternalLink size={13} />
                </a>
              )}
              <div className="mt-3 space-y-2">
                <p className="text-xs font-semibold text-slate-900">Top JD-Relevant Projects</p>
                {(candidate.verified_links.github_analysis?.top_projects || []).map((project, pi) => (
                  <div key={pi} className="rounded-md border border-slate-200 bg-white p-2">
                    <p className="text-xs font-semibold text-slate-900">{project.repo_name} · {project.complexity_score}/10 {project.complexity_label}</p>
                    <p className="text-[11px] text-slate-600">{project.project_type} | JD Match: {project.jd_stack_coverage_pct}% | Stars: {project.stars}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* ── ATS tab ── */}
        {activeTab === "ATS" && (
          <div className="space-y-3" data-testid={`candidate-ats-panel-${cid}`}>
            <div className="flex items-center gap-3">
              <ATSBadge label={ats.label || "Green"} score={ats.score ?? 100} />
              <span className="text-sm text-slate-600">ATS Compatibility Score</span>
            </div>
            {(ats.issues || []).length > 0 && (
              <div className="rounded-md border border-red-200 bg-red-50 p-3">
                <p className="text-xs font-semibold text-red-800 mb-1">Issues Detected</p>
                <ul className="list-disc pl-4 space-y-1">
                  {ats.issues.map((issue, i) => <li key={i} className="text-xs text-red-700">{issue}</li>)}
                </ul>
              </div>
            )}
            {(ats.suggestions || []).length > 0 && (
              <div className="rounded-md border border-amber-200 bg-amber-50 p-3">
                <p className="text-xs font-semibold text-amber-800 mb-1">Fix Suggestions</p>
                <ul className="list-disc pl-4 space-y-1">
                  {ats.suggestions.map((s, i) => <li key={i} className="text-xs text-amber-700">{s}</li>)}
                </ul>
              </div>
            )}
            {(ats.issues || []).length === 0 && (
              <p className="text-xs text-green-700 font-medium">✅ No ATS issues detected — resume is parser-friendly.</p>
            )}
          </div>
        )}

        {/* ── Trust tab ── */}
        {activeTab === "Trust" && (
          <div className="space-y-3" data-testid={`candidate-trust-panel-${cid}`}>
            <div className="flex items-center gap-3">
              <span className="text-2xl">{trust.badge || "🟢"}</span>
              <div>
                <p className="text-sm font-semibold text-slate-900">Trust Score: {trust.score ?? 100}%</p>
                <p className="text-xs text-slate-500">Credential integrity: {trust.label || "High"}</p>
              </div>
            </div>
            {(trust.flags || []).length > 0 ? (
              <div className="rounded-md border border-amber-200 bg-amber-50 p-3">
                <p className="text-xs font-semibold text-amber-800 mb-1">Flags for Review</p>
                <ul className="list-disc pl-4 space-y-1">
                  {trust.flags.map((f, i) => <li key={i} className="text-xs text-amber-700">{f}</li>)}
                </ul>
              </div>
            ) : (
              <p className="text-xs text-green-700 font-medium">✅ No credibility concerns detected.</p>
            )}
            {(bias.flags || []).length > 0 && (
              <div className="rounded-md border border-purple-200 bg-purple-50 p-3">
                <p className="text-xs font-semibold text-purple-800 mb-1">Bias & Fairness Signals</p>
                {bias.flags.map((f, i) => <p key={i} className="text-xs text-purple-700">{f}</p>)}
                <p className="mt-1 text-xs text-purple-600 italic">{bias.diversity_note}</p>
              </div>
            )}
          </div>
        )}

        {/* ── Career tab ── */}
        {activeTab === "Career" && (
          <div className="space-y-3" data-testid={`candidate-career-panel-${cid}`}>
            <div className="flex items-center gap-3">
              <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${
                career.label === "Rising" ? "border-green-200 bg-green-50 text-green-700"
                  : career.label === "Stable" ? "border-blue-200 bg-blue-50 text-blue-700"
                  : "border-slate-200 bg-slate-50 text-slate-600"
              }`}>
                {career.label || "Stable"} · {career.score ?? 0}/100
              </span>
              <span className="text-sm text-slate-600">Career Trajectory</span>
            </div>
            <div className="w-full rounded-full bg-slate-200 h-2">
              <div className="h-2 rounded-full bg-[#eb6a45] transition-all" style={{ width: `${career.score ?? 0}%` }} />
            </div>
            {(career.notes || []).length > 0 ? (
              <ul className="list-disc pl-4 space-y-1">
                {career.notes.map((n, i) => <li key={i} className="text-xs text-slate-700">{n}</li>)}
              </ul>
            ) : (
              <p className="text-xs text-slate-500">No significant trajectory signals detected in resume text.</p>
            )}
          </div>
        )}

        {/* ── Notable Achievements tab ── */}
        {activeTab === "Notable Achievements" && (() => {
          const na = candidate.notable_achievements || {};
          const list = na.top_achievements || [];
          const typeLabel = {
            github: "GitHub Repo",
            hackathon: "Hackathon / Competition",
            certification: "Certification",
            linkedin: "LinkedIn",
            portfolio: "Portfolio Project",
          };
          const typeIcon = {
            github: "💻",
            hackathon: "🎖️",
            certification: "📜",
            linkedin: "🔗",
            portfolio: "🌐",
          };
          return (
            <div className="space-y-3" data-testid={`candidate-achievements-panel-${cid}`}>
              <div className="flex items-center justify-between">
                <p className="text-sm font-semibold text-slate-900">
                  Notable Achievements
                  <span className="ml-2 rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-500">
                    {list.length} found · {na.total_found ?? 0} total scanned
                  </span>
                </p>
              </div>

              {list.length === 0 ? (
                <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-5 text-center">
                  <p className="text-xs text-slate-400 italic">No notable achievements detected for this candidate.</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {list.map((ach, i) => (
                    <div key={i} className="flex items-start gap-3 rounded-lg border border-slate-200 bg-white p-3">
                      {/* Medal */}
                      <span className="mt-0.5 text-xl shrink-0">{ach.medal}</span>

                      {/* Content */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <p className="text-sm font-semibold text-slate-800 leading-tight truncate">
                              {ach.title}
                            </p>
                            <p className="mt-0.5 text-[11px] text-slate-400">
                              {typeIcon[ach.achievement_type] || "📌"}{" "}
                              {typeLabel[ach.achievement_type] || ach.achievement_type}
                              {ach.stars > 0 && ` · ${ach.stars}⭐`}
                              {ach.forks > 0 && ` · ${ach.forks} forks`}
                            </p>
                            {ach.description && ach.description !== ach.title && (
                              <p className="mt-1 text-[11px] text-slate-500 leading-relaxed line-clamp-2">
                                {ach.description}
                              </p>
                            )}
                          </div>

                          {/* Score + link */}
                          <div className="flex flex-col items-end gap-1 shrink-0">
                            <span className="text-xs font-bold text-slate-700">{ach.score}/100</span>
                            {(ach.url || ach.deployed_url) && (
                              <a
                                href={ach.deployed_url || ach.url}
                                target="_blank"
                                rel="noreferrer"
                                className="inline-flex items-center gap-1 text-[10px] text-blue-600 hover:underline"
                              >
                                {ach.deployed_url ? "🔗 Live" : "View"} <ExternalLink size={10} />
                              </a>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })()}

        {/* ── Email tab ── */}
        {activeTab === "Email" && (
          <div className="space-y-3" data-testid={`candidate-email-panel-${cid}`}>
            <div className="flex items-center justify-between">
              <p className="text-sm font-semibold text-slate-900">
                Email Template —{" "}
                <span className={`text-xs font-medium ${email.template_type === "advance" ? "text-green-600" : email.template_type === "waitlist" ? "text-amber-600" : "text-red-600"}`}>
                  {email.template_type === "advance" ? "🎉 Advance" : email.template_type === "waitlist" ? "⏳ Waitlist" : "❌ Reject"}
                </span>
              </p>
              <button
                type="button"
                onClick={() => copyToClipboard(`Subject: ${email.subject}\n\n${email.body}`)}
                className="inline-flex items-center gap-1 rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-600 hover:border-[#eb6a45] hover:text-[#eb6a45]"
              >
                <Copy size={12} /> Copy
              </button>
            </div>
            <div className="rounded-md border border-slate-200 bg-white p-3 space-y-2">
              <p className="text-xs font-semibold text-slate-700">Subject: {email.subject}</p>
              <pre className="whitespace-pre-wrap text-xs text-slate-600 font-sans leading-relaxed">{email.body}</pre>
            </div>
          </div>
        )}


        {/* ── Portfolio tab ── */}
        {activeTab === "Portfolio" && (
          <div className="space-y-5" data-testid={`candidate-portfolio-panel-${cid}`}>

            {/* ── GitHub Profile Header ── */}
            {candidate.verified_links.github_analysis?.verified ? (
              <div className="flex flex-wrap items-center gap-4 rounded-xl border border-slate-200 bg-white p-4">
                <img
                  src={`https://github.com/${candidate.verified_links.github_analysis.username}.png?size=64`}
                  alt="GitHub avatar"
                  className="h-14 w-14 rounded-full border-2 border-slate-200"
                  onError={(e) => { e.target.style.display = "none"; }}
                />
                <div className="flex-1 min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-base font-bold text-slate-900">
                      @{candidate.verified_links.github_analysis.username}
                    </p>
                    <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                      candidate.verified_links.github_analysis.activity_status === "Active"
                        ? "bg-green-100 text-green-700"
                        : candidate.verified_links.github_analysis.activity_status === "Recent"
                          ? "bg-blue-100 text-blue-700"
                          : "bg-slate-100 text-slate-500"
                    }`}>
                      {candidate.verified_links.github_analysis.activity_status}
                    </span>
                  </div>
                  <div className="mt-1 flex flex-wrap gap-3 text-xs text-slate-500">
                    <span>📦 {candidate.verified_links.github_analysis.total_public_repos} repos</span>
                    <span>🔍 {candidate.verified_links.github_analysis.repos_analyzed} analyzed</span>
                    <span>🎯 {candidate.verified_links.github_analysis.jd_relevant_projects} JD-relevant</span>
                    <span>📊 {candidate.verified_links.github_analysis.stack_coverage_pct}% stack coverage</span>
                    <span>🏆 Best complexity: {candidate.verified_links.github_analysis.best_project_complexity}/10</span>
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {(candidate.verified_links.github.top_languages || []).map((lang) => (
                      <span key={lang} className="rounded-md bg-slate-100 px-2 py-0.5 font-mono text-[10px] text-slate-600">{lang}</span>
                    ))}
                  </div>
                </div>
                <a href={candidate.verified_links.github_url} target="_blank" rel="noreferrer"
                  className="inline-flex items-center gap-1 rounded-full border border-slate-800 bg-slate-900 px-4 py-2 text-xs font-semibold text-white hover:bg-slate-700">
                  View Profile <ExternalLink size={12} />
                </a>
              </div>
            ) : (
              <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4 text-xs text-slate-500">
                No verified GitHub profile detected for this candidate.
              </div>
            )}

            {/* ── GitHub Major Projects ── */}
            {(candidate.verified_links.github_analysis?.top_projects || []).length > 0 && (
              <div>
                <p className="mb-3 text-sm font-bold text-slate-900">
                  Major GitHub Projects
                  <span className="ml-2 rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-500">
                    sorted by complexity & JD relevance
                  </span>
                </p>
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {candidate.verified_links.github_analysis.top_projects.map((project, pi) => (
                    <a
                      key={pi}
                      href={project.repo_url}
                      target="_blank"
                      rel="noreferrer"
                      className="group block rounded-xl border border-slate-200 bg-white p-4 shadow-sm transition-all hover:border-[#eb6a45] hover:shadow-md"
                    >
                      {/* Repo header */}
                      <div className="flex items-start justify-between gap-2">
                        <p className="font-mono text-sm font-bold text-slate-900 group-hover:text-[#eb6a45] truncate">
                          {project.repo_name}
                        </p>
                        <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                          project.activity_status === "Active" ? "bg-green-100 text-green-700"
                            : project.activity_status === "Recent" ? "bg-blue-100 text-blue-700"
                            : "bg-slate-100 text-slate-400"
                        }`}>
                          {project.activity_status}
                        </span>
                      </div>

                      {/* Complexity bar */}
                      <div className="mt-2 flex items-center gap-2">
                        <div className="h-1.5 flex-1 rounded-full bg-slate-100">
                          <div
                            className="h-1.5 rounded-full bg-[#eb6a45]"
                            style={{ width: `${(project.complexity_score / 10) * 100}%` }}
                          />
                        </div>
                        <span className="text-[10px] font-semibold text-slate-700 shrink-0">
                          {project.complexity_score}/10 {project.complexity_label}
                        </span>
                      </div>

                      {/* Description */}
                      {project.description && (
                        <p className="mt-2 text-[11px] text-slate-600 leading-relaxed line-clamp-2">
                          {project.description}
                        </p>
                      )}

                      {/* README preview */}
                      {project.readme_preview && !project.description && (
                        <p className="mt-2 text-[11px] text-slate-500 leading-relaxed line-clamp-2 italic">
                          {project.readme_preview}
                        </p>
                      )}

                      {/* Tech stack */}
                      <div className="mt-2 flex flex-wrap gap-1">
                        {(project.tech_stack || []).slice(0, 5).map((tech) => {
                          const isJdMatch = (project.jd_matched_tech || []).includes(tech);
                          return (
                            <span key={tech} className={`rounded px-1.5 py-0.5 font-mono text-[10px] ${
                              isJdMatch ? "bg-green-100 text-green-700 font-semibold" : "bg-slate-100 text-slate-500"
                            }`}>
                              {tech}
                            </span>
                          );
                        })}
                      </div>

                      {/* Stats row */}
                      <div className="mt-3 flex flex-wrap gap-3 text-[10px] text-slate-500 border-t border-slate-100 pt-2">
                        <span>⭐ {project.stars}</span>
                        <span>🍴 {project.forks}</span>
                        <span>👥 {project.contributors}</span>
                        <span>🎯 JD: {project.jd_stack_coverage_pct}%</span>
                        {project.tests_present && <span className="text-green-600">✅ Tests</span>}
                        {project.deployment_ready && <span className="text-blue-600">🚀 Deploy-ready</span>}
                      </div>
                    </a>
                  ))}
                </div>
              </div>
            )}

            {/* ── LinkedIn Section ── */}
            {candidate.verified_links.linkedin_analysis?.verified ? (
              <div className="space-y-3">
                {/* LinkedIn header */}
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-blue-200 bg-blue-50 p-4">
                  <div>
                    <div className="flex items-center gap-2">
                      <svg className="h-5 w-5 text-blue-700" fill="currentColor" viewBox="0 0 24 24">
                        <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/>
                      </svg>
                      <p className="text-sm font-bold text-blue-900">LinkedIn Profile Verified</p>
                    </div>
                    {candidate.verified_links.linkedin_analysis.headline && (
                      <p className="mt-1 text-xs text-blue-700 line-clamp-2">
                        {candidate.verified_links.linkedin_analysis.headline}
                      </p>
                    )}
                    <div className="mt-2 flex flex-wrap gap-3 text-[10px] text-blue-600">
                      <span>🧑‍💼 {candidate.verified_links.linkedin_analysis.total_experience_years}yr exp</span>
                      <span>🔗 {candidate.verified_links.linkedin_analysis.connections_count}+ connections</span>
                      <span>📁 {candidate.verified_links.linkedin_analysis.projects_found} project mentions</span>
                      {candidate.verified_links.linkedin_analysis.premium_detected && <span>⭐ Premium</span>}
                    </div>
                  </div>
                  <a href={candidate.verified_links.linkedin_url} target="_blank" rel="noreferrer"
                    className="inline-flex items-center gap-1 rounded-full border border-blue-700 bg-blue-700 px-4 py-2 text-xs font-semibold text-white hover:bg-blue-800">
                    View Profile <ExternalLink size={12} />
                  </a>
                </div>

                {/* JD keywords found on LinkedIn */}
                {(candidate.verified_links.linkedin_analysis.jd_keywords_found || []).length > 0 && (
                  <div className="rounded-xl border border-green-200 bg-green-50 p-3">
                    <p className="text-xs font-semibold text-green-800 mb-2">JD Keywords Found on LinkedIn</p>
                    <div className="flex flex-wrap gap-1">
                      {candidate.verified_links.linkedin_analysis.jd_keywords_found.map((kw) => (
                        <span key={kw} className="rounded-md bg-green-100 px-2 py-0.5 font-mono text-[10px] font-semibold text-green-700">{kw}</span>
                      ))}
                    </div>
                  </div>
                )}

                {/* Achievements */}
                {(candidate.verified_links.linkedin_analysis.achievements || []).length > 0 && (
                  <div className="rounded-xl border border-amber-200 bg-amber-50 p-3">
                    <p className="text-xs font-semibold text-amber-800 mb-2">🏆 Achievements & Honors</p>
                    <ul className="space-y-1">
                      {candidate.verified_links.linkedin_analysis.achievements.map((a, i) => (
                        <li key={i} className="text-[11px] text-amber-800 leading-relaxed">• {a}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Certifications */}
                {(candidate.verified_links.linkedin_analysis.certifications || []).length > 0 && (
                  <div className="rounded-xl border border-purple-200 bg-purple-50 p-3">
                    <p className="text-xs font-semibold text-purple-800 mb-2">🎓 Certifications</p>
                    <div className="flex flex-wrap gap-2">
                      {candidate.verified_links.linkedin_analysis.certifications.map((cert, i) => (
                        <span key={i} className="rounded-md border border-purple-200 bg-white px-2 py-1 text-[10px] text-purple-700">{cert}</span>
                      ))}
                    </div>
                  </div>
                )}

                {/* LinkedIn project titles */}
                {(candidate.verified_links.linkedin_analysis.project_titles || []).length > 0 && (
                  <div className="rounded-xl border border-slate-200 bg-white p-3">
                    <p className="text-xs font-semibold text-slate-800 mb-2">📁 Projects Listed on LinkedIn</p>
                    <ul className="space-y-1">
                      {candidate.verified_links.linkedin_analysis.project_titles.map((title, i) => (
                        <li key={i} className="text-[11px] text-slate-600">• {title}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Verification score */}
                <div className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-3">
                  <div className="flex-1">
                    <p className="text-[10px] text-slate-500 mb-1">LinkedIn Verification Score</p>
                    <div className="h-2 rounded-full bg-slate-100">
                      <div className="h-2 rounded-full bg-blue-500" style={{ width: `${Math.min(100, (candidate.verified_links.linkedin_analysis.verification_score / 30) * 100)}%` }} />
                    </div>
                  </div>
                  <span className="text-sm font-bold text-blue-700">{candidate.verified_links.linkedin_analysis.verification_score}/30</span>
                </div>
              </div>
            ) : (
              <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4 text-xs text-slate-500">
                No LinkedIn profile detected or profile is private/unavailable.
              </div>
            )}

            {/* ── All Scanned Links ── */}
            {(candidate.verified_links.scanned_links || []).length > 0 && (
              <div>
                <p className="mb-2 text-xs font-semibold text-slate-900">All Scanned Links</p>
                <div className="space-y-1">
                  {candidate.verified_links.scanned_links.map((lnk, i) => (
                    <p key={i} className="truncate font-mono text-[11px] text-slate-500" title={lnk.url}>
                      <span className={lnk.reachable ? "text-green-600" : "text-red-400"}>{lnk.reachable ? "✓" : "✕"}</span>
                      {" "}[{lnk.link_type}] {lnk.url}
                    </p>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </td>
    </tr>
  );
}

export default function ResultsPage() {
  const { batchId: routeBatchId } = useParams();
  const batchId =
    routeBatchId === "latest" ? localStorage.getItem("last_resume_batch_id") : routeBatchId;
  const { token } = useAuth();

  const [analysis, setAnalysis] = useState(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [tierFilter, setTierFilter] = useState("all");
  const [sortOrder, setSortOrder] = useState("desc");
  const [expandedCandidateId, setExpandedCandidateId] = useState("");
  const [emailModalCandidate, setEmailModalCandidate] = useState(null);
  const [sentEmails, setSentEmails] = useState({}); // { candidate_id: timestamp }

  useEffect(() => {
    const fetchResults = async () => {
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
      } catch (error) {
        toast.error(error?.response?.data?.detail || "Unable to fetch screening results.");
      } finally {
        setLoading(false);
      }
    };

    fetchResults();
  }, [batchId, token]);

  const filteredResults = useMemo(() => {
    if (!analysis?.results?.length) return [];

    const filtered = analysis.results.filter((candidate) => {
      const matchSearch = candidate.candidate_name.toLowerCase().includes(search.toLowerCase());
      const matchTier = tierFilter === "all" || candidate.tier === tierFilter;
      return matchSearch && matchTier;
    });

    return filtered.sort((a, b) =>
      sortOrder === "desc" ? b.fit_score - a.fit_score : a.fit_score - b.fit_score,
    );
  }, [analysis, search, tierFilter, sortOrder]);

  if (!batchId) {
    return (
      <Card className="premium-card border-none" data-testid="results-empty-batch-card">
        <CardContent className="p-6 text-slate-600" data-testid="results-empty-batch-text">
          No batch found yet. Please upload resumes first.
        </CardContent>
      </Card>
    );
  }

  if (loading) {
    return (
      <Card className="premium-card border-none" data-testid="results-loading-card">
        <CardContent className="p-6 text-slate-600" data-testid="results-loading-text">
          Loading ranked candidates...
        </CardContent>
      </Card>
    );
  }

  if (!analysis) {
    return (
      <Card className="premium-card border-none" data-testid="results-no-data-card">
        <CardContent className="p-6 text-slate-600" data-testid="results-no-data-text">
          We could not retrieve this analysis batch.
        </CardContent>
      </Card>
    );
  }

  return (
    <section className="space-y-6" data-testid="results-page">
      <div className="premium-card border-none px-5 py-5 md:px-6" data-testid="results-hero-strip">
        <div className="grid gap-3 lg:grid-cols-[1.4fr_1fr]" data-testid="results-hero-grid">
          <p className="text-2xl font-semibold text-slate-900" data-testid="results-hero-title">
            Smart hiring cockpit: ranked evidence, verified links, and project-backed confidence.
          </p>
          <p className="text-sm text-slate-500 md:text-base" data-testid="results-hero-description">
            Balanced view for recruiters — score, skill fit, portfolio proof, and shortlist signals in one glance.
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-start justify-between gap-4" data-testid="results-header-row">
        <div>
          <h2 className="text-4xl sm:text-5xl lg:text-6xl" data-testid="results-main-heading">
            Ranked Candidates
          </h2>
          <p className="mt-2 text-sm text-slate-600 md:text-base" data-testid="results-subheading">
            Batch: <span className="font-mono text-xs text-slate-900">{batchId}</span>
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2" data-testid="results-header-actions">
          <Button
            type="button"
            variant="outline"
            className="rounded-full border-slate-300 bg-white hover:border-[#eb6a45] hover:text-[#eb6a45]"
            onClick={async () => {
              try {
                const res = await axios.get(
                  `${process.env.REACT_APP_BACKEND_URL}/api/screener/export/${batchId}`,
                  { headers: { Authorization: `Bearer ${token}` }, responseType: "blob" }
                );
                const url = URL.createObjectURL(res.data);
                const a = document.createElement("a");
                a.href = url;
                a.download = `results-${batchId}.csv`;
                a.click();
                URL.revokeObjectURL(url);
              } catch {
                toast.error("Export failed. Please try again.");
              }
            }}
            data-testid="export-csv-button"
          >
            <Download size={15} /> Export CSV
          </Button>

          <Link to={`/analytics/${batchId}`} data-testid="go-to-analytics-link">
            <Button type="button" className="rounded-full bg-[#eb6a45] text-white hover:bg-[#d7552f]" data-testid="go-to-analytics-button">
              Open Analytics
            </Button>
          </Link>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4" data-testid="results-kpi-grid">
        <KpiCard
          title="Resumes Uploaded"
          value={analysis.analytics.resumes_uploaded}
          helper="Total processed from ZIP"
          testId="kpi-resumes-uploaded"
        />
        <KpiCard
          title="Average Fit Score"
          value={`${analysis.analytics.average_fit_score}%`}
          helper="Across all candidates"
          testId="kpi-average-fit"
        />
        <KpiCard
          title="Above 80%"
          value={analysis.analytics.candidates_above_80}
          helper="Strong-match shortlist"
          testId="kpi-above-80"
        />
        <KpiCard
          title="Required Skills"
          value={analysis.required_skills.length}
          helper="Detected from JD"
          testId="kpi-required-skills"
        />
      </div>

      <Card className="premium-card border-none" data-testid="results-table-card">
        <CardHeader className="space-y-4">
          <CardTitle className="text-xl" data-testid="results-table-title">
            Candidate Ranking Table
          </CardTitle>

          <div className="grid gap-3 md:grid-cols-[2fr_1fr_1fr]" data-testid="results-filter-grid">
            <Input
              placeholder="Search candidate name"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              data-testid="candidate-search-input"
            />

            <select
              className="h-10 rounded-full border border-slate-300 bg-white px-3 text-sm"
              value={tierFilter}
              onChange={(event) => setTierFilter(event.target.value)}
              data-testid="tier-filter-select"
            >
              <option value="all">All tiers</option>
              <option value="Top Tier">Top Tier</option>
              <option value="Middle Tier">Middle Tier</option>
              <option value="Low Tier">Low Tier</option>
            </select>

            <Button
              type="button"
              variant="outline"
              className="rounded-full border-slate-300 bg-white hover:border-[#eb6a45] hover:text-[#eb6a45]"
              onClick={() => setSortOrder((old) => (old === "desc" ? "asc" : "desc"))}
              data-testid="sort-by-fit-button"
            >
              Sort by fit: {sortOrder === "desc" ? "High → Low" : "Low → High"}
            </Button>
          </div>
        </CardHeader>

        <CardContent className="soft-scrollbar overflow-auto">
          <Table data-testid="candidate-results-table">
            <TableHeader>
              <TableRow data-testid="candidate-results-header-row">
                <TableHead data-testid="header-candidate-name">Candidate</TableHead>
                <TableHead data-testid="header-fit-score">Fit Score</TableHead>
                <TableHead data-testid="header-tier">Tier</TableHead>
                <TableHead data-testid="header-verification">Verification</TableHead>
                <TableHead data-testid="header-achievements">Achievements</TableHead>
                <TableHead data-testid="header-subscores">Sub-scores</TableHead>
                <TableHead data-testid="header-github">Verified Links & Activity</TableHead>
                <TableHead data-testid="header-email">Email</TableHead>
                <TableHead data-testid="header-actions">Details</TableHead>
              </TableRow>
            </TableHeader>

            <TableBody>
              {filteredResults.map((candidate, index) => {
                const isOpen = expandedCandidateId === candidate.candidate_id;

                return (
                  <Fragment key={candidate.candidate_id}>
                    <motion.tr
                      initial={{ opacity: 0, y: 10 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.2, delay: index * 0.03 }}
                    >
                      <TableCell data-testid={`candidate-name-${candidate.candidate_id}`}>
                        <p className="font-semibold text-slate-900" data-testid={`candidate-name-text-${candidate.candidate_id}`}>
                          {candidate.candidate_name}
                        </p>
                        <p className="font-mono text-xs text-slate-500" data-testid={`candidate-source-file-${candidate.candidate_id}`}>
                          {candidate.source_file}
                        </p>
                      </TableCell>

                      <TableCell data-testid={`candidate-fit-score-${candidate.candidate_id}`}>
                        <ScoreBadge score={candidate.fit_score} testId={`fit-score-badge-${candidate.candidate_id}`} />
                      </TableCell>

                      <TableCell data-testid={`candidate-tier-${candidate.candidate_id}`}>
                        <TierTag tier={candidate.tier} testId={`tier-tag-${candidate.candidate_id}`} />
                      </TableCell>

                      <TableCell data-testid={`candidate-verification-${candidate.candidate_id}`}>
                        <VerificationBadge candidate={candidate} />
                      </TableCell>

                      <TableCell data-testid={`candidate-achievements-${candidate.candidate_id}`}>
                        {(() => {
                          const list = candidate.notable_achievements?.top_achievements || [];
                          if (!list.length) return <p className="text-[11px] text-slate-400 italic">None</p>;
                          const typeIcon = { github: "💻", hackathon: "🎖️", certification: "📜", linkedin: "🔗", portfolio: "🌐" };
                          return (
                            <div className="space-y-1">
                              {list.slice(0, 2).map((a, i) => (
                                <div key={i} className="flex items-center gap-1">
                                  <span className="text-xs">{a.medal}</span>
                                  <span className="text-[11px] text-slate-600 truncate max-w-[120px]" title={a.title}>
                                    {typeIcon[a.achievement_type] || "📌"} {a.title}
                                  </span>
                                </div>
                              ))}
                              {list.length > 2 && (
                                <p className="text-[10px] text-slate-400">+{list.length - 2} more</p>
                              )}
                            </div>
                          );
                        })()}
                      </TableCell>

                      <TableCell data-testid={`candidate-subscores-${candidate.candidate_id}`}>
                        <p className="text-xs text-slate-600" data-testid={`candidate-skills-score-${candidate.candidate_id}`}>
                          Skills: {candidate.skills_match_score}%
                        </p>
                        <p className="text-xs text-slate-600" data-testid={`candidate-experience-score-${candidate.candidate_id}`}>
                          Experience: {candidate.experience_match_score}%
                        </p>
                        <p className="text-xs text-slate-600" data-testid={`candidate-education-score-${candidate.candidate_id}`}>
                          Education: {candidate.education_match_score}%
                        </p>
                      </TableCell>

                      <TableCell data-testid={`candidate-links-${candidate.candidate_id}`}>
                        <p className="text-xs text-slate-600" data-testid={`candidate-github-user-${candidate.candidate_id}`}>
                          GitHub: {candidate.verified_links.github.username || "N/A"}
                        </p>
                        <p className="text-xs text-slate-600" data-testid={`candidate-portfolio-bonus-${candidate.candidate_id}`}>
                          Portfolio bonus: +{candidate.verified_links.smart_portfolio?.verification_bonus || 0}%
                        </p>
                        <p className="text-xs text-slate-600" data-testid={`candidate-jd-projects-${candidate.candidate_id}`}>
                          JD projects: {candidate.verified_links.github_analysis?.jd_relevant_projects || 0}
                        </p>
                        <p className="text-xs text-slate-600" data-testid={`candidate-linkedin-count-${candidate.candidate_id}`}>
                          LinkedIn links: {candidate.verified_links.linkedin_urls?.length || (candidate.verified_links.linkedin_url ? 1 : 0)}
                        </p>
                        <p className="text-xs text-slate-600" data-testid={`candidate-links-scanned-${candidate.candidate_id}`}>
                          Links scanned: {candidate.verified_links.scanned_links?.length || 0}
                        </p>
                        <p className="text-xs text-slate-600" data-testid={`candidate-github-repos-${candidate.candidate_id}`}>
                          Repos: {candidate.verified_links.github.repo_count}
                        </p>
                        <p className="text-xs text-slate-600" data-testid={`candidate-activity-bonus-${candidate.candidate_id}`}>
                          Activity bonus: +{candidate.verified_links.activity_bonus}%
                        </p>
                        <p className="mt-1 text-xs font-medium" data-testid={`candidate-extraction-method-${candidate.candidate_id}`}>
                          {candidate.github_extraction_method === "llm-groq"
                            ? <span className="text-violet-600">🤖 LLM (Llama 3.1 70B)</span>
                            : <span className="text-green-600">✅ Rule-based</span>}
                        </p>
                      </TableCell>

                      <TableCell data-testid={`candidate-email-cell-${candidate.candidate_id}`}>
                        <div className="flex flex-col gap-1.5">
                          {candidate.candidate_email ? (
                            <p className="max-w-[160px] truncate font-mono text-[11px] text-slate-500" title={candidate.candidate_email}>
                              {candidate.candidate_email}
                            </p>
                          ) : (
                            <p className="text-[11px] text-slate-400 italic">No email found</p>
                          )}
                          <Button
                            type="button"
                            size="sm"
                            onClick={() => setEmailModalCandidate(candidate)}
                            className="rounded-full bg-[#eb6a45] text-white hover:bg-[#d7552f] text-xs px-3"
                            data-testid={`send-email-button-${candidate.candidate_id}`}
                          >
                            <Mail size={12} className="mr-1" /> Send
                          </Button>
                          {sentEmails[candidate.candidate_id] && (
                            <span className="flex items-center gap-1 text-[10px] text-green-600 font-medium">
                              <Clock size={9} /> Sent today
                            </span>
                          )}
                        </div>
                      </TableCell>

                      <TableCell data-testid={`candidate-expand-cell-${candidate.candidate_id}`}>
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          onClick={() =>
                            setExpandedCandidateId((old) =>
                              old === candidate.candidate_id ? "" : candidate.candidate_id,
                            )
                          }
                          data-testid={`candidate-expand-button-${candidate.candidate_id}`}
                        >
                          {isOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />} Expand
                        </Button>
                      </TableCell>
                    </motion.tr>

                    {isOpen && (
                      <CandidateDetailPanel
                        candidate={candidate}
                        analysis={analysis}
                      />
                    )}
                  </Fragment>
                );
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {emailModalCandidate && (
        <EmailModal
          candidate={emailModalCandidate}
          token={token}
          onClose={(sent) => {
            if (sent) {
              setSentEmails((prev) => ({ ...prev, [emailModalCandidate.candidate_id]: Date.now() }));
            }
            setEmailModalCandidate(null);
          }}
        />
      )}
    </section>
  );
}
