"use client";
import { useState, useCallback, useEffect, useRef } from "react";
import { useDropzone } from "react-dropzone";
import { api } from "@/lib/api";

function getTenantId(): string {
  return localStorage.getItem("tenantId") || "";
}

interface UploadResult {
  file: string;
  status: string;
  docId?: string;
}

interface PipelineEvent {
  node: string;
  status: string;
  detail: string;
  timestamp: string;
}

export default function UploadPage() {
  const [patientId, setPatientId] = useState("");
  const [patients, setPatients] = useState<Array<{ id: string; name: string }>>([]);
  const [uploading, setUploading] = useState(false);
  const [results, setResults] = useState<UploadResult[]>([]);
  const [activePipeline, setActivePipeline] = useState<string | null>(null);
  const [pipelineEvents, setPipelineEvents] = useState<PipelineEvent[]>([]);
  const eventSourceRef = useRef<EventSource | null>(null);

  const MONITOR_API = "http://localhost:8001";
  const nodeSteps = ["planner", "extract_lab_data", "extract_prescription", "extract_claims_csv",
    "verifier", "persist", "chunk_embed", "pipeline"];

  useEffect(() => {
    loadPatients();
    return () => { closeSSE(); };
  }, []);

  async function loadPatients() {
    try {
      const data = await api.listPatients(getTenantId());
      setPatients(data.map((p: any) => ({ id: p.id, name: p.name || p.id })));
    } catch {}
  }

  function openSSE(documentId: string) {
    closeSSE();
    setActivePipeline(documentId);
    setPipelineEvents([]);

    const es = new EventSource(`${MONITOR_API}/monitor/documents/${documentId}/stream`);
    es.addEventListener("agent_event", (e) => {
      try {
        const event = JSON.parse(e.data);
        const newEvent = {
          node: event.node,
          status: event.status,
          detail: event.detail || "",
          timestamp: new Date(event.timestamp * 1000).toISOString(),
        };
        setPipelineEvents((prev) => [...prev, newEvent]);

        // Close on terminal
        if (event.status === "completed" || event.status === "failed") {
          if (event.node === "pipeline") {
            setTimeout(closeSSE, 1000);
          }
        }
      } catch {}
    });
    es.onerror = () => {};
    eventSourceRef.current = es;
  }

  function closeSSE() {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setActivePipeline(null);
  }

  const onDrop = useCallback(
    async (acceptedFiles: File[]) => {
      if (!patientId.trim()) {
        alert("Select a patient first");
        return;
      }
      setUploading(true);
      const newResults: UploadResult[] = [];

      for (const file of acceptedFiles) {
        try {
          const res = await api.uploadDocument(getTenantId(), patientId, file);
          newResults.push({ file: file.name, status: "queued", docId: res.document_id });
          openSSE(res.document_id);
        } catch (e: any) {
          newResults.push({ file: file.name, status: `error: ${e.message}` });
        }
      }
      setResults((prev) => [...prev, ...newResults]);
      setUploading(false);
    },
    [patientId]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { "application/pdf": [".pdf"], "text/csv": [".csv"] },
    maxSize: 20 * 1024 * 1024,
  });

  function isNodeComplete(node: string): boolean {
    return pipelineEvents.some((e) => e.node === node && e.status === "completed");
  }

  function isNodeActive(node: string): boolean {
    return pipelineEvents.some((e) => e.node === node && e.status === "started") && !isNodeComplete(node);
  }

  function isNodeFailed(node: string): boolean {
    return pipelineEvents.some((e) => e.node === node && e.status === "failed");
  }

  return (
    <div className="max-w-2xl mx-auto">
      <h2 className="text-2xl font-bold text-gray-900 mb-2">Upload Documents</h2>
      <p className="text-gray-500 mb-6">Upload lab reports, prescriptions, or insurance claims</p>

      <div className="mb-6">
        <label className="block text-sm font-medium text-gray-700 mb-1">Patient</label>
        <select value={patientId} onChange={(e) => setPatientId(e.target.value)}
          className="w-full border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-primary-500 focus:outline-none bg-white">
          <option value="">Select a patient</option>
          {patients.map((p) => (
            <option key={p.id} value={p.id}>{p.name} ({p.id})</option>
          ))}
        </select>
      </div>

      <div {...getRootProps()}
        className={`border-2 border-dashed rounded-xl p-12 text-center cursor-pointer transition-colors ${
          isDragActive ? "border-primary-500 bg-primary-50" : "border-gray-300 hover:border-gray-400"
        }`}>
        <input {...getInputProps()} />
        <div className="text-gray-400 mb-2">
          <svg className="w-12 h-12 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
              d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
          </svg>
        </div>
        <p className="text-gray-600">{isDragActive ? "Drop files here..." : "Drag & drop PDFs or CSVs, or click to browse"}</p>
        <p className="text-xs text-gray-400 mt-2">Max 20MB per file</p>
      </div>

      {uploading && <div className="mt-4 text-center text-primary-600">Uploading...</div>}

      {/* Live Pipeline View */}
      {activePipeline && pipelineEvents.length > 0 && (
        <div className="mt-6 bg-white rounded-xl border p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-medium text-gray-900 text-sm">Live Pipeline: {activePipeline.slice(0, 8)}...</h3>
            <button onClick={closeSSE} className="text-xs text-gray-400 hover:text-gray-600">Close</button>
          </div>

          <div className="flex gap-1 mb-4">
            {nodeSteps.map((step) => (
              <div key={step} className={`flex-1 text-center px-1 py-1.5 rounded text-[10px] font-medium transition-colors ${
                isNodeFailed(step) ? "bg-red-100 text-red-700 animate-pulse" :
                isNodeComplete(step) ? "bg-green-100 text-green-700" :
                isNodeActive(step) ? "bg-blue-100 text-blue-700 ring-2 ring-blue-300" :
                "bg-gray-100 text-gray-400"
              }`}>
                {step.replace(/_/g, "\n")}
              </div>
            ))}
          </div>

          <div className="space-y-1 max-h-48 overflow-y-auto">
            {pipelineEvents.map((e, i) => (
              <div key={i} className="flex items-center gap-2 text-xs py-1 border-b border-gray-50 last:border-0">
                <span className={`w-2 h-2 rounded-full ${
                  e.status === "completed" ? "bg-green-500" :
                  e.status === "failed" ? "bg-red-500" :
                  e.status === "started" ? "bg-blue-500" : "bg-yellow-500"
                }`} />
                <span className="font-mono text-gray-400 w-16">{new Date(e.timestamp).toLocaleTimeString()}</span>
                <span className="font-medium text-gray-700">{e.node}</span>
                <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                  e.status === "completed" ? "bg-green-100 text-green-700" :
                  e.status === "failed" ? "bg-red-100 text-red-700" :
                  e.status === "started" ? "bg-blue-100 text-blue-700" : "bg-yellow-100 text-yellow-700"
                }`}>{e.status}</span>
                {e.detail && <span className="text-gray-400 truncate">{e.detail}</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {results.length > 0 && (
        <div className="mt-6 space-y-2">
          <h3 className="font-medium text-gray-900">Upload Results</h3>
          {results.map((r, i) => (
            <div key={i} className="flex justify-between items-center bg-white rounded-lg border p-3">
              <span className="text-sm text-gray-700">{r.file}</span>
              <div className="flex items-center gap-2">
                {r.docId && (
                  <button onClick={() => openSSE(r.docId!)}
                    className="text-xs text-primary-600 hover:text-primary-800 font-medium">
                    Watch Pipeline
                  </button>
                )}
                <span className={`text-sm font-medium ${
                  r.status === "queued" ? "text-medical-600" : "text-red-600"
                }`}>{r.status}</span>
              </div>
            </div>
          ))}
          <p className="text-xs text-gray-400 mt-2">
            Processing happens in the background. Watch the pipeline live above.
          </p>
        </div>
      )}
    </div>
  );
}

// Patient dropdown loads from backend API
