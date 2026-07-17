"use client";
import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";

function getTenantId(): string {
  return localStorage.getItem("tenantId") || "";
}

export default function DocumentDetailPage() {
  const { id: patientId, docId } = useParams<{ id: string; docId: string }>();
  const router = useRouter();
  const [doc, setDoc] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [showPreview, setShowPreview] = useState(false);
  const [csvData, setCsvData] = useState<string[][] | null>(null);
  const [csvLoading, setCsvLoading] = useState(false);
  const [fileUrl, setFileUrl] = useState<string | null>(null);

  useEffect(() => { loadDocument(); }, [docId]);

  async function loadDocument() {
    try {
      const data = await api.getDocument(getTenantId(), docId);
      setDoc(data);
      // Pre-fetch the file URL
      const fileData = await api.getDocumentFileUrl(getTenantId(), docId);
      setFileUrl(fileData.url);
    } catch (e) { console.error("Failed to load document:", e); }
    setLoading(false);
  }

  async function downloadFile() {
    if (!fileUrl) return;
    const a = document.createElement("a");
    a.href = fileUrl;
    a.download = doc?.filename || "document";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  async function openPreview() {
    setShowPreview(true);
    const ext = doc.filename?.split(".").pop()?.toLowerCase();
    if (ext === "csv" && fileUrl) {
      setCsvLoading(true);
      setCsvData(null);
      fetch(fileUrl)
        .then((r) => r.text())
        .then((text) => {
          const rows = text.split("\n").filter((r: string) => r.trim()).map((r: string) => r.split(","));
          setCsvData(rows);
        })
        .catch(() => setCsvData(null))
        .finally(() => setCsvLoading(false));
    }
  }

  function closePreview() {
    setShowPreview(false);
    setCsvData(null);
  }

  if (loading) return <div className="text-center py-12 text-gray-500">Loading document...</div>;
  if (!doc) return <div className="text-center py-12 text-gray-500">Document not found.</div>;

  const isCSV = doc.filename?.toLowerCase().endsWith(".csv");

  return (
    <div>
      {showPreview && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={closePreview}>
          <div className="bg-white rounded-xl shadow-2xl w-[90vw] h-[85vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between px-4 py-3 border-b">
              <span className="text-sm font-medium text-gray-700 truncate">{doc.filename}</span>
              <div className="flex items-center gap-2">
                <button onClick={downloadFile}
                  className="inline-flex items-center gap-1.5 bg-primary-600 text-white px-3 py-1.5 rounded-lg text-sm font-medium hover:bg-primary-700 transition-colors">
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                  Download
                </button>
                <button onClick={closePreview} className="text-gray-400 hover:text-gray-600 p-1">
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            </div>
            <div className="flex-1 overflow-auto bg-gray-100">
              {isCSV ? (
                csvLoading ? (
                  <div className="flex items-center justify-center h-full text-gray-500">Loading CSV...</div>
                ) : csvData && csvData.length > 0 ? (
                  <div className="p-4 overflow-auto h-full">
                    <table className="w-full text-sm border-collapse">
                      <thead>
                        <tr>
                          {csvData[0]?.map((header: string, i: number) => (
                            <th key={i} className="px-3 py-2 bg-gray-200 border text-left text-xs font-medium text-gray-600 whitespace-nowrap">
                              {header.trim()}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {csvData.slice(1).map((row, ri) => (
                          <tr key={ri} className="border-b last:border-0">
                            {row.map((cell: string, ci: number) => (
                              <td key={ci} className="px-3 py-2 border text-gray-700 whitespace-nowrap">
                                {cell.trim()}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="flex items-center justify-center h-full text-gray-500">No data</div>
                )
              ) : fileUrl ? (
                <iframe src={fileUrl} className="w-full h-full border-0" title={doc.filename} />
              ) : (
                <div className="flex items-center justify-center h-full text-gray-500">Loading file...</div>
              )}
            </div>
          </div>
        </div>
      )}

      <div className="mb-6">
        <div className="flex items-center justify-between">
          <button onClick={() => router.push(`/patients/${patientId}`)}
            className="text-sm text-primary-600 hover:text-primary-800 mb-2 inline-flex items-center gap-1">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
            Back to Patient
          </button>
          <button onClick={openPreview}
            className="inline-flex items-center gap-1.5 bg-primary-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-primary-700 transition-colors">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
            </svg>
            View Original
          </button>
        </div>
        <h2 className="text-2xl font-bold text-gray-900">{doc.filename}</h2>
        <div className="flex items-center gap-3 mt-1 text-sm text-gray-500">
          <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
            doc.status === "processed" ? "bg-green-100 text-green-700" :
            doc.status === "failed" ? "bg-red-100 text-red-700" :
            "bg-yellow-100 text-yellow-700"
          }`}>{doc.status}</span>
          {doc.doc_type && <span className="text-gray-400">Type: {doc.doc_type}</span>}
          {doc.created_at && <span className="text-gray-400">Uploaded: {new Date(doc.created_at).toLocaleString()}</span>}
          <span className="text-gray-400">{doc.chunks_count} chunks indexed</span>
        </div>
      </div>

      {doc.lab_results && doc.lab_results.length > 0 && (
        <div className="bg-white rounded-xl border overflow-hidden mb-6">
          <div className="px-4 py-3 bg-gray-50 border-b font-medium text-gray-700">Lab Results ({doc.lab_results.length})</div>
          <table className="w-full text-sm">
            <thead className="border-b">
              <tr>
                <th className="text-left px-4 py-2 font-medium text-gray-600">Test</th>
                <th className="text-left px-4 py-2 font-medium text-gray-600">Value</th>
                <th className="text-left px-4 py-2 font-medium text-gray-600">Unit</th>
                <th className="text-left px-4 py-2 font-medium text-gray-600">Ref Range</th>
                <th className="text-left px-4 py-2 font-medium text-gray-600">Status</th>
                <th className="text-left px-4 py-2 font-medium text-gray-600">Date</th>
              </tr>
            </thead>
            <tbody>
              {doc.lab_results.map((l: any) => (
                <tr key={l.id} className="border-b last:border-0">
                  <td className="px-4 py-2 font-medium">{l.test_name}</td>
                  <td className="px-4 py-2">{l.value}</td>
                  <td className="px-4 py-2 text-gray-500">{l.unit}</td>
                  <td className="px-4 py-2 text-gray-500">{l.reference_range}</td>
                  <td className="px-4 py-2">
                    {l.flagged_abnormal ? (
                      <span className="bg-red-100 text-red-700 px-2 py-0.5 rounded-full text-xs font-medium">Abnormal</span>
                    ) : (
                      <span className="bg-green-100 text-green-700 px-2 py-0.5 rounded-full text-xs font-medium">Normal</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-gray-500">{l.test_date ? new Date(l.test_date).toLocaleDateString() : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {doc.prescriptions && doc.prescriptions.length > 0 && (
        <div className="bg-white rounded-xl border overflow-hidden mb-6">
          <div className="px-4 py-3 bg-gray-50 border-b font-medium text-gray-700">Prescriptions ({doc.prescriptions.length})</div>
          <table className="w-full text-sm">
            <thead className="border-b">
              <tr>
                <th className="text-left px-4 py-2 font-medium text-gray-600">Drug</th>
                <th className="text-left px-4 py-2 font-medium text-gray-600">Dosage</th>
                <th className="text-left px-4 py-2 font-medium text-gray-600">Frequency</th>
                <th className="text-left px-4 py-2 font-medium text-gray-600">Doctor</th>
                <th className="text-left px-4 py-2 font-medium text-gray-600">Date</th>
              </tr>
            </thead>
            <tbody>
              {doc.prescriptions.map((r: any) => (
                <tr key={r.id} className="border-b last:border-0">
                  <td className="px-4 py-2 font-medium">{r.drug_name}</td>
                  <td className="px-4 py-2">{r.dosage}</td>
                  <td className="px-4 py-2">{r.frequency}</td>
                  <td className="px-4 py-2 text-gray-500">{r.prescribing_doctor || "-"}</td>
                  <td className="px-4 py-2 text-gray-500">{r.prescribed_date ? new Date(r.prescribed_date).toLocaleDateString() : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {doc.claims && doc.claims.length > 0 && (
        <div className="bg-white rounded-xl border overflow-hidden mb-6">
          <div className="px-4 py-3 bg-gray-50 border-b font-medium text-gray-700">Claims ({doc.claims.length})</div>
          <table className="w-full text-sm">
            <thead className="border-b">
              <tr>
                <th className="text-left px-4 py-2 font-medium text-gray-600">Procedure</th>
                <th className="text-left px-4 py-2 font-medium text-gray-600">Amount</th>
                <th className="text-left px-4 py-2 font-medium text-gray-600">Status</th>
                <th className="text-left px-4 py-2 font-medium text-gray-600">Date</th>
              </tr>
            </thead>
            <tbody>
              {doc.claims.map((c: any) => (
                <tr key={c.id} className="border-b last:border-0">
                  <td className="px-4 py-2 font-medium">{c.procedure_code}</td>
                  <td className="px-4 py-2">${c.claim_amount?.toFixed(2) || "-"}</td>
                  <td className="px-4 py-2">
                    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                      c.claim_status === "approved" ? "bg-green-100 text-green-700" :
                      c.claim_status === "denied" ? "bg-red-100 text-red-700" :
                      "bg-yellow-100 text-yellow-700"
                    }`}>{c.claim_status}</span>
                  </td>
                  <td className="px-4 py-2 text-gray-500">{c.claim_date ? new Date(c.claim_date).toLocaleDateString() : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {doc.clinical_notes && doc.clinical_notes.length > 0 && (
        <div className="bg-white rounded-xl border overflow-hidden mb-6">
          <div className="px-4 py-3 bg-gray-50 border-b font-medium text-gray-700">Clinical Notes ({doc.clinical_notes.length})</div>
          <div className="p-4 space-y-2">
            {doc.clinical_notes.map((note: any, i: number) => (
              <div key={i} className="bg-gray-50 rounded-lg p-3 text-sm text-gray-700">
                {typeof note === "string" ? note : JSON.stringify(note)}
              </div>
            ))}
          </div>
        </div>
      )}

      {doc.processing_traces && doc.processing_traces.length > 0 && (
        <div className="bg-white rounded-xl border overflow-hidden mb-6">
          <div className="px-4 py-3 bg-gray-50 border-b font-medium text-gray-700">Processing Pipeline ({doc.processing_traces.length} stages)</div>
          <div className="p-4 space-y-2">
            {doc.processing_traces.map((t: any, i: number) => (
              <div key={i} className="flex items-start gap-3 text-sm">
                <span className="px-2 py-0.5 bg-blue-100 text-blue-700 rounded text-xs font-medium whitespace-nowrap">{t.stage}</span>
                <div className="flex-1">
                  {t.output_summary && <p className="text-gray-700">{t.output_summary}</p>}
                  {t.latency_ms != null && <p className="text-gray-400 text-xs">{t.latency_ms}ms</p>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {doc.extracted_text && (
        <div className="bg-white rounded-xl border overflow-hidden mb-6">
          <div className="px-4 py-3 bg-gray-50 border-b font-medium text-gray-700">Extracted Text</div>
          <div className="p-4">
            <pre className="text-sm text-gray-700 whitespace-pre-wrap font-sans leading-relaxed">{doc.extracted_text}</pre>
          </div>
        </div>
      )}

      {!doc.lab_results?.length && !doc.prescriptions?.length && !doc.claims?.length && !doc.clinical_notes?.length && (
        <div className="bg-white rounded-xl border p-8 text-center text-gray-400">
          No extracted data available for this document.
        </div>
      )}
    </div>
  );
}
