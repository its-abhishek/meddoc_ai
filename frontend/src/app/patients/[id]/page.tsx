"use client";
import { useEffect, useState, useRef, useCallback } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";

function getTenantId(): string {
  return localStorage.getItem("tenantId") || "";
}

interface LabResult {
  id: string; test_name: string; value: number; unit: string;
  reference_range: string; flagged_abnormal: boolean; test_date: string;
}
interface Prescription {
  id: string; drug_name: string; dosage: string; frequency: string;
  prescribed_date: string; prescribing_doctor: string;
}
interface Claim {
  id: string; procedure_code: string; claim_amount: number;
  claim_status: string; claim_date: string;
}
interface RiskFlag {
  id: string; flag_type: string; severity: string;
  description: string; source: string; status: string; created_at: string;
}
interface ReportBlock {
  id: string; block_type: string; order_index: number;
  content: string; ai_generated: boolean; edited_by_user: boolean;
}
interface Report {
  id: string; status: string; generated_at: string; last_edited_at: string | null;
  blocks: ReportBlock[];
}
interface TrendPoint {
  date: string; value: number; unit: string; reference_range: string; flagged_abnormal: boolean;
}
interface TrendData {
  test_name: string; trend_direction: string; time_series: TrendPoint[]; commentary: string;
}

export default function PatientDetailPage() {
  const { id: patientId } = useParams<{ id: string }>();
  const [patient, setPatient] = useState<any>(null);
  const [labs, setLabs] = useState<LabResult[]>([]);
  const [prescriptions, setPrescriptions] = useState<Prescription[]>([]);
  const [claims, setClaims] = useState<Claim[]>([]);
  const [riskFlags, setRiskFlags] = useState<RiskFlag[]>([]);
  const [summary, setSummary] = useState<string>("");
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [chatMessages, setChatMessages] = useState<Array<{ role: string; content: string; sources?: any[] }>>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<string>("labs");
  const chatEndRef = useRef<HTMLDivElement>(null);

  // Report state
  const [report, setReport] = useState<Report | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [editingBlock, setEditingBlock] = useState<string | null>(null);
  const [editContent, setEditContent] = useState("");

  // Trend state
  const [trendTest, setTrendTest] = useState("");
  const [trendData, setTrendData] = useState<TrendData | null>(null);
  const [trendLoading, setTrendLoading] = useState(false);

  // Notification state
  const [notifications, setNotifications] = useState<any[]>([]);
  const [copied, setCopied] = useState(false);
  const [pdfDownloading, setPdfDownloading] = useState(false);
  const [documents, setDocuments] = useState<any[]>([]);
  const [docsLoading, setDocsLoading] = useState(false);

  useEffect(() => { loadData(); }, [patientId]);
  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [chatMessages]);

  async function loadData() {
    try {
      const [p, l, r, c, f, n] = await Promise.all([
        api.getPatient(getTenantId(), patientId),
        api.getLabResults(getTenantId(), patientId),
        api.getPrescriptions(getTenantId(), patientId),
        api.getClaims(getTenantId(), patientId),
        api.getRiskFlags(getTenantId(), patientId),
        api.getNotifications(getTenantId()).catch(() => []),
      ]);
      setPatient(p); setLabs(l); setPrescriptions(r); setClaims(c);
      setRiskFlags(f); setNotifications(n);
    } catch (e) { console.error("Failed to load patient data:", e); }
  }

  async function loadDocuments() {
    setDocsLoading(true);
    try {
      const data = await api.listDocuments(getTenantId(), patientId);
      setDocuments(data);
    } catch (e) { console.error("Failed to load documents:", e); }
    setDocsLoading(false);
  }

  async function deleteDoc(docId: string) {
    if (!confirm("Delete this document and all extracted data?")) return;
    try {
      await api.deleteDocument(getTenantId(), docId);
      setDocuments((prev) => prev.filter((d: any) => d.id !== docId));
    } catch (e: any) {
      alert("Delete failed: " + e.message);
    }
  }

  async function loadSummary() {
    setSummaryLoading(true);
    try {
      const data = await api.getSummary(getTenantId(), patientId);
      setSummary(data.summary);
    } catch (e) { setSummary("Failed to load summary."); }
    setSummaryLoading(false);
  }

  async function sendChat() {
    if (!chatInput.trim() || chatLoading) return;
    const question = chatInput.trim();
    setChatInput("");
    setChatMessages((prev) => [...prev, { role: "user", content: question }]);
    setChatLoading(true);
    try {
      const data = await api.query(getTenantId(), patientId, question);
      setChatMessages((prev) => [...prev, { role: "assistant", content: data.answer, sources: data.source_chunks }]);
    } catch (e) {
      setChatMessages((prev) => [...prev, { role: "assistant", content: "Error processing query." }]);
    }
    setChatLoading(false);
  }

  // Report actions
  async function loadOrGenerateReport() {
    setReportLoading(true);
    try {
      const gen = await api.generateReport(getTenantId(), patientId);
      const r = await api.getReport(getTenantId(), gen.report_id);
      setReport(r);
    } catch (e: any) {
      console.error("Report error:", e);
    }
    setReportLoading(false);
  }

  function startEditBlock(block: ReportBlock) {
    setEditingBlock(block.id);
    setEditContent(block.content);
  }

  async function saveEditBlock(blockId: string) {
    if (!report) return;
    await api.updateReportBlock(getTenantId(), report.id, blockId, editContent);
    setEditingBlock(null);
    const r = await api.getReport(getTenantId(), report.id);
    setReport(r);
  }

  async function regenerateBlock(blockId: string) {
    if (!report) return;
    await api.regenerateBlock(getTenantId(), report.id, blockId);
    const r = await api.getReport(getTenantId(), report.id);
    setReport(r);
  }

  async function addCustomNote() {
    if (!report) return;
    const content = prompt("Enter note content:");
    if (!content) return;
    await api.addCustomBlock(getTenantId(), report.id, content);
    const r = await api.getReport(getTenantId(), report.id);
    setReport(r);
  }

  async function finalizeReport() {
    if (!report) return;
    await api.finalizeReport(getTenantId(), report.id);
    const r = await api.getReport(getTenantId(), report.id);
    setReport(r);
  }

  // Trends
  async function loadTrend() {
    if (!trendTest.trim()) return;
    setTrendLoading(true);
    try {
      const data = await api.getTrends(getTenantId(), patientId, trendTest);
      setTrendData(data);
    } catch (e) { console.error("Trend error:", e); }
    setTrendLoading(false);
  }

  // Risk flags
  async function dismissFlag(flagId: string) {
    const reason = prompt("Reason for dismissal:");
    if (!reason) return;
    await api.dismissFlag(getTenantId(), patientId, flagId, reason);
    const flags = await api.getRiskFlags(getTenantId(), patientId);
    setRiskFlags(flags);
  }

  async function acknowledgeFlag(flagId: string) {
    await api.acknowledgeFlag(getTenantId(), patientId, flagId);
    const flags = await api.getRiskFlags(getTenantId(), patientId);
    setRiskFlags(flags);
  }

  async function copySummary() {
    try {
      await navigator.clipboard.writeText(summary);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {}
  }

  async function downloadPDF() {
    if (!report) return;
    setPdfDownloading(true);
    try {
      await api.downloadReportPDF(getTenantId(), report.id);
    } catch (e) { console.error("PDF download failed:", e); }
    setPdfDownloading(false);
  }

  if (!patient) return <div className="text-center py-12 text-gray-500">Loading patient...</div>;

  const tabs = [
    { key: "docs", label: `Documents (${documents.length})` },
    { key: "labs", label: `Lab Results (${labs.length})` },
    { key: "rx", label: `Prescriptions (${prescriptions.length})` },
    { key: "claims", label: `Claims (${claims.length})` },
    { key: "flags", label: `Risk Flags (${riskFlags.filter(f => f.status === "open").length})` },
    { key: "trends", label: "Trends" },
    { key: "chat", label: "Query" },
    { key: "summary", label: "Summary" },
    { key: "report", label: "Report" },
  ];

  return (
    <div>
      <div className="mb-6">
        <h2 className="text-2xl font-bold text-gray-900">{patient.name}</h2>
        <p className="text-sm text-gray-500">
          ID: {patientId} {patient.external_ref && `| Ref: ${patient.external_ref}`}
        </p>
      </div>

      <div className="flex gap-1 border-b mb-6 flex-wrap">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => {
              setActiveTab(t.key);
              if (t.key === "summary" && !summary) loadSummary();
              if (t.key === "docs" && documents.length === 0) loadDocuments();
            }}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
              activeTab === t.key
                ? "border-primary-600 text-primary-600"
                : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Documents */}
      {activeTab === "docs" && (
        <div className="bg-white rounded-xl border overflow-hidden">
          {docsLoading ? (
            <div className="text-center py-8 text-gray-500">Loading documents...</div>
          ) : documents.length === 0 ? (
            <div className="text-center py-8 text-gray-400">No documents uploaded yet.</div>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b">
                <tr>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Filename</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Type</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Status</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Uploaded</th>
                  <th className="text-right px-4 py-3 font-medium text-gray-600">Action</th>
                </tr>
              </thead>
              <tbody>
                {documents.map((d: any) => (
                  <tr key={d.id} className="border-b last:border-0">
                    <td className="px-4 py-3 font-medium">{d.filename}</td>
                    <td className="px-4 py-3 text-gray-500">{d.doc_type || "-"}</td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                        d.status === "processed" ? "bg-green-100 text-green-700" :
                        d.status === "failed" ? "bg-red-100 text-red-700" :
                        "bg-yellow-100 text-yellow-700"
                      }`}>{d.status}</span>
                    </td>
                    <td className="px-4 py-3 text-gray-500">
                      {d.created_at ? new Date(d.created_at).toLocaleDateString() : "-"}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <button onClick={() => deleteDoc(d.id)}
                        className="text-xs text-red-500 hover:text-red-700 font-medium">Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* Lab Results */}
      {activeTab === "labs" && (
        <div className="bg-white rounded-xl border overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="text-left px-4 py-3 font-medium text-gray-600">Test</th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">Value</th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">Unit</th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">Ref Range</th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">Status</th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">Date</th>
              </tr>
            </thead>
            <tbody>
              {labs.map((l) => (
                <tr key={l.id} className="border-b last:border-0">
                  <td className="px-4 py-3 font-medium">{l.test_name}</td>
                  <td className="px-4 py-3">{l.value}</td>
                  <td className="px-4 py-3 text-gray-500">{l.unit}</td>
                  <td className="px-4 py-3 text-gray-500">{l.reference_range}</td>
                  <td className="px-4 py-3">
                    {l.flagged_abnormal ? (
                      <span className="bg-red-100 text-red-700 px-2 py-0.5 rounded-full text-xs font-medium">Abnormal</span>
                    ) : (
                      <span className="bg-green-100 text-green-700 px-2 py-0.5 rounded-full text-xs font-medium">Normal</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-gray-500">
                    {l.test_date ? new Date(l.test_date).toLocaleDateString() : "-"}
                  </td>
                </tr>
              ))}
              {labs.length === 0 && (
                <tr><td colSpan={6} className="px-4 py-8 text-center text-gray-400">No lab results</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Prescriptions */}
      {activeTab === "rx" && (
        <div className="bg-white rounded-xl border overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="text-left px-4 py-3 font-medium text-gray-600">Drug</th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">Dosage</th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">Frequency</th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">Doctor</th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">Date</th>
              </tr>
            </thead>
            <tbody>
              {prescriptions.map((r) => (
                <tr key={r.id} className="border-b last:border-0">
                  <td className="px-4 py-3 font-medium">{r.drug_name}</td>
                  <td className="px-4 py-3">{r.dosage}</td>
                  <td className="px-4 py-3">{r.frequency}</td>
                  <td className="px-4 py-3 text-gray-500">{r.prescribing_doctor || "-"}</td>
                  <td className="px-4 py-3 text-gray-500">
                    {r.prescribed_date ? new Date(r.prescribed_date).toLocaleDateString() : "-"}
                  </td>
                </tr>
              ))}
              {prescriptions.length === 0 && (
                <tr><td colSpan={5} className="px-4 py-8 text-center text-gray-400">No prescriptions</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Claims */}
      {activeTab === "claims" && (
        <div className="bg-white rounded-xl border overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="text-left px-4 py-3 font-medium text-gray-600">Procedure</th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">Amount</th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">Status</th>
                <th className="text-left px-4 py-3 font-medium text-gray-600">Date</th>
              </tr>
            </thead>
            <tbody>
              {claims.map((c) => (
                <tr key={c.id} className="border-b last:border-0">
                  <td className="px-4 py-3 font-medium">{c.procedure_code}</td>
                  <td className="px-4 py-3">${c.claim_amount?.toFixed(2) || "-"}</td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                      c.claim_status === "approved" ? "bg-green-100 text-green-700" :
                      c.claim_status === "denied" ? "bg-red-100 text-red-700" : "bg-yellow-100 text-yellow-700"
                    }`}>{c.claim_status}</span>
                  </td>
                  <td className="px-4 py-3 text-gray-500">
                    {c.claim_date ? new Date(c.claim_date).toLocaleDateString() : "-"}
                  </td>
                </tr>
              ))}
              {claims.length === 0 && (
                <tr><td colSpan={4} className="px-4 py-8 text-center text-gray-400">No claims</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Risk Flags */}
      {activeTab === "flags" && (
        <div className="space-y-3">
          {riskFlags.length === 0 ? (
            <div className="text-center py-12 bg-white rounded-xl border">
              <p className="text-gray-500">No risk flags for this patient.</p>
            </div>
          ) : (
            riskFlags.map((f) => (
              <div key={f.id} className="bg-white rounded-xl border p-4">
                <div className="flex justify-between items-start mb-2">
                  <div className="flex items-center gap-2">
                    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                      f.severity === "high" ? "bg-red-100 text-red-700" :
                      f.severity === "medium" ? "bg-yellow-100 text-yellow-700" :
                      "bg-blue-100 text-blue-700"
                    }`}>{f.severity.toUpperCase()}</span>
                    <span className="font-medium text-gray-900">{f.flag_type}</span>
                    <span className={`text-xs px-2 py-0.5 rounded-full ${
                      f.status === "open" ? "bg-orange-100 text-orange-700" :
                      f.status === "acknowledged" ? "bg-blue-100 text-blue-700" :
                      "bg-gray-100 text-gray-500"
                    }`}>{f.status}</span>
                    <span className="text-xs text-gray-400">{f.source}</span>
                  </div>
                  {f.status === "open" && (
                    <div className="flex gap-2">
                      <button onClick={() => acknowledgeFlag(f.id)}
                        className="text-xs text-blue-600 hover:text-blue-800 font-medium">Acknowledge</button>
                      <button onClick={() => dismissFlag(f.id)}
                        className="text-xs text-red-600 hover:text-red-800 font-medium">Dismiss</button>
                    </div>
                  )}
                </div>
                <p className="text-sm text-gray-600">{f.description}</p>
              </div>
            ))
          )}
        </div>
      )}

      {/* Trends */}
      {activeTab === "trends" && (
        <div className="bg-white rounded-xl border p-6">
          <div className="flex gap-2 mb-4">
            <input type="text" value={trendTest} onChange={(e) => setTrendTest(e.target.value)}
              placeholder="Test name (e.g. hemoglobin)"
              className="flex-1 border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-primary-500 focus:outline-none" />
            <button onClick={loadTrend} disabled={trendLoading}
              className="bg-primary-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-primary-700 disabled:opacity-50">
              Load Trend
            </button>
          </div>
          {trendLoading && <div className="text-center py-4 text-gray-500">Loading...</div>}
          {trendData && (
            <div>
              <div className="mb-4">
                <h3 className="text-lg font-semibold">{trendData.test_name} Trend</h3>
                <p className="text-sm text-gray-500">Trend: {trendData.trend_direction} | Unit: {trendData.time_series[0]?.unit}</p>
              </div>
              <div className="space-y-2 mb-4">
                {trendData.time_series.map((p, i) => (
                  <div key={i} className="flex items-center gap-4 text-sm">
                    <span className="w-24 text-gray-500">{p.date ? new Date(p.date).toLocaleDateString() : "N/A"}</span>
                    <div className="flex-1 h-6 bg-gray-100 rounded-full overflow-hidden">
                      <div className={`h-full rounded-full ${
                        p.flagged_abnormal ? "bg-red-400" : "bg-green-400"
                      }`} style={{ width: `${Math.min(100, (p.value / 200) * 100)}%` }} />
                    </div>
                    <span className="w-20 text-right font-medium">{p.value} {p.unit}</span>
                    <span className="text-gray-400 text-xs">{p.reference_range}</span>
                    {p.flagged_abnormal && <span className="text-red-600 text-xs font-medium">ABNORMAL</span>}
                  </div>
                ))}
              </div>
              <div className="bg-gray-50 rounded-lg p-3 text-sm italic text-gray-600 border">
                {trendData.commentary}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Chat / Query */}
      {activeTab === "chat" && (
        <div className="bg-white rounded-xl border flex flex-col" style={{ height: "500px" }}>
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {chatMessages.length === 0 && (
              <div className="text-center text-gray-400 py-8">
                Ask questions about this patient's medical documents.
              </div>
            )}
            {chatMessages.map((msg, i) => (
              <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                <div className={`max-w-[80%] rounded-xl px-4 py-2 text-sm ${
                  msg.role === "user" ? "bg-primary-600 text-white" : "bg-gray-100 text-gray-900"
                }`}>
                  {msg.content}
                  {msg.sources && msg.sources.length > 0 && (
                    <div className="mt-2 text-xs opacity-70 border-t pt-1">
                      Sources: {msg.sources.length} chunks
                    </div>
                  )}
                </div>
              </div>
            ))}
            {chatLoading && (
              <div className="flex justify-start">
                <div className="bg-gray-100 rounded-xl px-4 py-2 text-sm text-gray-500">Thinking...</div>
              </div>
            )}
            <div ref={chatEndRef} />
          </div>
          <div className="border-t p-4 flex gap-2">
            <input type="text" value={chatInput} onChange={(e) => setChatInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && sendChat()}
              placeholder="Ask about medications, lab results, etc."
              className="flex-1 border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-primary-500 focus:outline-none" />
            <button onClick={sendChat} disabled={chatLoading}
              className="bg-primary-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-primary-700 disabled:opacity-50">Ask</button>
          </div>
        </div>
      )}

      {/* Summary */}
      {activeTab === "summary" && (
        <div className="bg-white rounded-xl border p-6">
          {summaryLoading ? (
            <div className="text-center py-8 text-gray-500">Generating summary...</div>
          ) : summary ? (
            <div>
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-lg font-semibold text-gray-900">Clinical Summary</h3>
                <button onClick={copySummary}
                  className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-700 border border-gray-200 rounded-lg px-3 py-1.5 hover:bg-gray-50 transition-colors">
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    {copied ? (
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    ) : (
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                    )}
                  </svg>
                  {copied ? "Copied!" : "Copy"}
                </button>
              </div>
              <div className="prose prose-sm max-w-none text-gray-700 leading-relaxed">
                {summary.split("\n").map((line, i) => {
                  if (line.startsWith("## ")) return <h2 key={i} className="text-lg font-bold mt-4 mb-2 text-gray-900">{line.slice(3)}</h2>;
                  if (line.startsWith("### ")) return <h3 key={i} className="text-base font-semibold mt-3 mb-1 text-gray-800">{line.slice(4)}</h3>;
                  if (line.startsWith("- ") || line.startsWith("* ")) return <li key={i} className="ml-4 text-gray-700">{line.slice(2)}</li>;
                  if (line.startsWith("**") && line.endsWith("**")) return <p key={i} className="font-bold mt-2 text-gray-900">{line.slice(2, -2)}</p>;
                  if (line.includes("**")) {
                    const parts = line.split(/(\*\*[^*]+\*\*)/g);
                    return <p key={i} className="my-0.5">{parts.map((part, j) =>
                      part.startsWith("**") && part.endsWith("**")
                        ? <strong key={j} className="font-semibold text-gray-900">{part.slice(2, -2)}</strong>
                        : part
                    )}</p>;
                  }
                  if (line.trim() === "") return <br key={i} />;
                  return <p key={i} className="my-0.5">{line}</p>;
                })}
              </div>
            </div>
          ) : (
            <div className="text-center py-8">
              <p className="text-gray-500 mb-4">No summary generated yet.</p>
              <button onClick={loadSummary}
                className="bg-primary-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-primary-700">Generate Summary</button>
            </div>
          )}
        </div>
      )}

      {/* Report Editing Canvas */}
      {activeTab === "report" && (
        <div>
          {!report ? (
            <div className="text-center py-12 bg-white rounded-xl border">
              <p className="text-gray-500 mb-4">Generate an AI-powered clinical report for this patient.</p>
              <button onClick={loadOrGenerateReport} disabled={reportLoading}
                className="bg-primary-600 text-white px-6 py-2 rounded-lg text-sm font-medium hover:bg-primary-700 disabled:opacity-50">
                {reportLoading ? "Generating..." : "Generate Report"}
              </button>
            </div>
          ) : (
            <div>
              <div className="flex items-center justify-between mb-4">
                <div>
                  <span className="text-sm text-gray-500">Status: </span>
                  <span className={`text-sm font-medium px-2 py-0.5 rounded-full ${
                    report.status === "finalized" ? "bg-green-100 text-green-700" : "bg-yellow-100 text-yellow-700"
                  }`}>{report.status}</span>
                  <span className="text-xs text-gray-400 ml-2">
                    Generated: {new Date(report.generated_at).toLocaleString()}
                  </span>
                </div>
                <div className="flex gap-2">
                  <button onClick={addCustomNote}
                    className="border border-gray-300 text-gray-700 px-3 py-1.5 rounded-lg text-sm font-medium hover:bg-gray-50">
                    + Add Note
                  </button>
                  <button onClick={downloadPDF} disabled={pdfDownloading}
                    className="border border-gray-300 text-gray-700 px-3 py-1.5 rounded-lg text-sm font-medium hover:bg-gray-50 disabled:opacity-50 flex items-center gap-1.5">
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                    </svg>
                    {pdfDownloading ? "Downloading..." : "Download PDF"}
                  </button>
                  {report.status !== "finalized" && (
                    <button onClick={finalizeReport}
                      className="bg-green-600 text-white px-3 py-1.5 rounded-lg text-sm font-medium hover:bg-green-700">
                      Finalize
                    </button>
                  )}
                </div>
              </div>

              <div className="space-y-3">
                {report.blocks.map((block) => (
                  <div key={block.id} className="bg-white rounded-xl border p-4">
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-medium text-gray-500 uppercase tracking-wider">
                          {block.block_type.replace(/_/g, " ")}
                        </span>
                        {block.ai_generated && !block.edited_by_user && (
                          <span className="text-xs bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded">AI</span>
                        )}
                        {block.edited_by_user && (
                          <span className="text-xs bg-green-100 text-green-700 px-1.5 py-0.5 rounded">Edited</span>
                        )}
                      </div>
                      {editingBlock !== block.id && (
                        <div className="flex gap-2">
                          <button onClick={() => startEditBlock(block)}
                            className="text-xs text-primary-600 hover:text-primary-800">Edit</button>
                          <button onClick={() => regenerateBlock(block.id)}
                            className="text-xs text-orange-600 hover:text-orange-800">Regenerate</button>
                        </div>
                      )}
                    </div>

                    {editingBlock === block.id ? (
                      <div>
                        <textarea value={editContent} onChange={(e) => setEditContent(e.target.value)}
                          className="w-full border rounded-lg p-2 text-sm min-h-[100px] focus:ring-2 focus:ring-primary-500 focus:outline-none" />
                        <div className="flex gap-2 mt-2">
                          <button onClick={() => saveEditBlock(block.id)}
                            className="bg-primary-600 text-white px-3 py-1 rounded text-xs font-medium hover:bg-primary-700">Save</button>
                          <button onClick={() => setEditingBlock(null)}
                            className="border border-gray-300 text-gray-700 px-3 py-1 rounded text-xs font-medium hover:bg-gray-50">Cancel</button>
                        </div>
                      </div>
                    ) : (
                      <div className="text-sm text-gray-700 whitespace-pre-wrap">{block.content}</div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Summary rendered with markdown parsing
