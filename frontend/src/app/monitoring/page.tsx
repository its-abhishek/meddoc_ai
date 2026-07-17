"use client";
import { useEffect, useState, useRef, useCallback } from "react";
import { api } from "@/lib/api";
import ConfirmDeleteDialog from "@/components/ConfirmDeleteDialog";

function getTenantId(): string {
  return localStorage.getItem("tenantId") || "";
}

interface DocSummary {
  document_id: string;
  filename?: string;
  current_node: string;
  current_status: string;
  detail?: string;
  last_event: string;
}

interface StatusEvent {
  node: string;
  status: string;
  detail: string;
  timestamp: string;
}

interface DocStatus {
  document_id: string;
  current_node: string;
  current_status: string;
  events: StatusEvent[];
}

export default function MonitoringPage() {
  const [activeDocs, setActiveDocs] = useState<DocSummary[]>([]);
  const [allDocs, setAllDocs] = useState<DocSummary[]>([]);
  const [selectedDoc, setSelectedDoc] = useState<string | null>(null);
  const [allEvents, setAllEvents] = useState<StatusEvent[]>([]);
  const [docIdInput, setDocIdInput] = useState("");
  const [pipelineDone, setPipelineDone] = useState(false);
  const [rateLimited, setRateLimited] = useState(false);
  const [retryCountdown, setRetryCountdown] = useState(0);
  const [documentToDelete, setDocumentToDelete] = useState<string | null>(null);
  const [deletingDocument, setDeletingDocument] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);
  const countdownRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 5000);
    return () => { clearInterval(interval); closeSSE(); if (countdownRef.current) clearInterval(countdownRef.current); };
  }, []);

  async function loadData() {
    const tid = getTenantId();
    try {
      const active = await api.getActiveDocuments(tid);
      setActiveDocs(active);
    } catch {}
    try {
      const all = await api.getAllDocuments(tid);
      setAllDocs(all);
    } catch {}
  }

  const openMonitor = useCallback(async (documentId: string) => {
    closeSSE();
    setSelectedDoc(documentId);
    setAllEvents([]);
    setPipelineDone(false);

    try {
      const status: DocStatus = await api.getDocumentStatus(documentId);
      const historical: StatusEvent[] = status.events.map((e) => ({
        node: e.node,
        status: e.status,
        detail: e.detail || "",
        timestamp: e.timestamp,
      }));
      setAllEvents(historical);

      const lastEvent = historical[historical.length - 1];
      if (lastEvent && lastEvent.node === "pipeline" &&
          (lastEvent.status === "completed" || lastEvent.status === "failed")) {
        setPipelineDone(true);
        return;
      }
    } catch {
      // No historical events yet
    }

    const es = new EventSource(`http://localhost:8001/monitor/documents/${documentId}/stream`);
    es.addEventListener("agent_event", (e) => {
      try {
        const event = JSON.parse(e.data);
        const newEvent: StatusEvent = {
          node: event.node,
          status: event.status,
          detail: event.detail || "",
          timestamp: new Date(event.timestamp * 1000).toISOString(),
        };
        setAllEvents((prev) => {
          const isDupe = prev.some(
            (pe) => pe.node === newEvent.node && pe.status === newEvent.status && pe.timestamp === newEvent.timestamp
          );
          if (isDupe) return prev;
          return [...prev, newEvent];
        });

        const detail = (event.detail || "").toLowerCase();
        if (detail.includes("rate limit") || detail.includes("429") || detail.includes("too many requests")) {
          setRateLimited(true);
          startCountdown(60);
        }

        if (event.node === "pipeline" && (event.status === "completed" || event.status === "failed")) {
          setPipelineDone(true);
          if (event.status === "failed" && detail.includes("rate limit")) {
            setRateLimited(true);
            startCountdown(60);
          }
        }
      } catch {}
    });
    es.onerror = () => {};
    eventSourceRef.current = es;
  }, []);

  function closeSSE() {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
  }

  function startCountdown(seconds: number) {
    setRetryCountdown(seconds);
    if (countdownRef.current) clearInterval(countdownRef.current);
    countdownRef.current = setInterval(() => {
      setRetryCountdown((prev) => {
        if (prev <= 1) {
          clearInterval(countdownRef.current!);
          setRateLimited(false);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
  }

  function lookupDoc() {
    if (docIdInput.trim()) {
      openMonitor(docIdInput.trim());
    }
  }

  function requestDeleteDocument(docId: string, e: React.MouseEvent) {
    e.stopPropagation();
    setDocumentToDelete(docId);
  }

  async function confirmDeleteDocument() {
    if (!documentToDelete) return;
    setDeletingDocument(true);
    try {
      await api.deleteDocument(getTenantId(), documentToDelete);
      setActiveDocs((prev) => prev.filter((d) => d.document_id !== documentToDelete));
      setAllDocs((prev) => prev.filter((d) => d.document_id !== documentToDelete));
      if (selectedDoc === documentToDelete) {
        closeSSE();
        setSelectedDoc(null);
        setAllEvents([]);
      }
      setDocumentToDelete(null);
    } catch (err: any) {
      alert("Delete failed: " + err.message);
    } finally {
      setDeletingDocument(false);
    }
  }

  const nodeSteps = ["planner", "extract_lab_data", "extract_prescription", "extract_claims_csv",
    "verifier", "persist", "chunk_embed", "pipeline"];

  function getStepState(step: string): "completed" | "failed" | "started" | "pending" {
    const completed = allEvents.some((e) => e.node === step && e.status === "completed");
    if (completed) return "completed";
    const failed = allEvents.some((e) => e.node === step && e.status === "failed");
    if (failed) return "failed";
    const started = allEvents.some((e) => e.node === step && e.status === "started");
    if (started) return "started";
    return "pending";
  }

  function statusColor(status: string) {
    if (status === "completed") return "bg-green-100 text-green-700";
    if (status === "failed") return "bg-red-100 text-red-700";
    if (status === "started") return "bg-blue-100 text-blue-700";
    return "bg-yellow-100 text-yellow-700";
  }

  return (
    <div>
      <h2 className="text-2xl font-bold text-gray-900 mb-2">Agent Activity</h2>
      <p className="text-gray-500 mb-6">Live view of document processing pipeline activity</p>

      {/* Document lookup */}
      <div className="flex gap-2 mb-6">
        <input type="text" value={docIdInput} onChange={(e) => setDocIdInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && lookupDoc()}
          placeholder="Enter document ID to monitor"
          className="flex-1 border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-primary-500 focus:outline-none" />
        <button onClick={lookupDoc}
          className="bg-primary-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-primary-700">Monitor</button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Document lists */}
        <div className="space-y-4">
          {/* Active */}
          <div className="bg-white rounded-xl border p-4">
            <h3 className="text-lg font-semibold mb-3">Active Pipeline Runs</h3>
            {activeDocs.length === 0 ? (
              <p className="text-sm text-gray-400 text-center py-4">No active pipeline runs</p>
            ) : (
              <div className="space-y-2">
                {activeDocs.map((d) => (
                  <button key={d.document_id} onClick={() => openMonitor(d.document_id)}
                    className={`w-full text-left p-3 rounded-lg border text-sm transition-colors ${
                      selectedDoc === d.document_id ? "border-primary-500 bg-primary-50" : "hover:bg-gray-50"
                    }`}>
                    <div className="flex justify-between items-center">
                      <span className="font-mono text-xs text-gray-500">{d.document_id.slice(0, 8)}...</span>
                      <div className="flex items-center gap-2">
                        <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${statusColor(d.current_status)}`}>
                          {d.current_node}
                        </span>
                        <span onClick={(e) => requestDeleteDocument(d.document_id, e)}
                          className="text-xs text-red-500 hover:text-red-700 font-medium cursor-pointer">Delete</span>
                      </div>
                    </div>
                    <p className="text-xs text-gray-400 mt-1">Last: {new Date(d.last_event).toLocaleTimeString()}</p>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* All recent */}
          <div className="bg-white rounded-xl border p-4">
            <h3 className="text-lg font-semibold mb-3">Recent Documents</h3>
            {allDocs.length === 0 ? (
              <p className="text-sm text-gray-400 text-center py-4">No documents yet</p>
            ) : (
              <div className="space-y-2 max-h-64 overflow-y-auto">
                {allDocs.map((d) => (
                  <button key={d.document_id} onClick={() => openMonitor(d.document_id)}
                    className={`w-full text-left p-3 rounded-lg border text-sm transition-colors ${
                      selectedDoc === d.document_id ? "border-primary-500 bg-primary-50" : "hover:bg-gray-50"
                    }`}>
                    <div className="flex justify-between items-center">
                      <span className="font-medium text-gray-700 text-xs truncate max-w-[140px]">{d.filename || d.document_id.slice(0, 8)}</span>
                      <div className="flex items-center gap-2">
                        <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${statusColor(d.current_status)}`}>
                          {d.current_status}
                        </span>
                        <span onClick={(e) => requestDeleteDocument(d.document_id, e)}
                          className="text-xs text-red-500 hover:text-red-700 font-medium cursor-pointer">Delete</span>
                      </div>
                    </div>
                    <p className="text-xs text-gray-400 mt-1">Step: {d.current_node} | {d.last_event ? new Date(d.last_event).toLocaleTimeString() : "pending"}</p>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Event stream */}
        <div className="bg-white rounded-xl border p-4">
          <h3 className="text-lg font-semibold mb-3">
            {selectedDoc ? `Events: ${selectedDoc.slice(0, 8)}...` : "Event Stream"}
          </h3>

          {rateLimited && (
            <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg">
              <div className="flex items-center gap-2">
                <svg className="w-5 h-5 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
                </svg>
                <div>
                  <p className="text-sm font-medium text-red-800">Rate limit exceeded — Groq API returned 429</p>
                  <p className="text-xs text-red-600 mt-0.5">
                    {retryCountdown > 0
                      ? `Retry available in ${retryCountdown}s — pipeline will resume automatically`
                      : "Retry available now — re-upload to try again"}
                  </p>
                </div>
                {retryCountdown > 0 && (
                  <div className="ml-auto w-10 h-10 rounded-full border-4 border-red-200 border-t-red-500 animate-spin flex items-center justify-center">
                    <span className="text-xs font-bold text-red-600">{retryCountdown}</span>
                  </div>
                )}
              </div>
            </div>
          )}

          {selectedDoc && (
            <div className="mb-4">
              <div className="flex gap-1 mb-3">
                {nodeSteps.map((step) => {
                  const state = getStepState(step);
                  return (
                    <div key={step} className={`flex-1 text-center px-1 py-1 rounded text-[10px] font-medium ${
                      state === "failed" ? "bg-red-100 text-red-700" :
                      state === "completed" ? "bg-green-100 text-green-700" :
                      state === "started" ? "bg-blue-100 text-blue-700" :
                      "bg-gray-100 text-gray-400"
                    }`}>
                      {step.replace(/_/g, " ")}
                    </div>
                  );
                })}
              </div>
              {pipelineDone && (
                <div className="text-xs text-gray-500 mb-2">
                  Pipeline finished &mdash; {allEvents.length} total events
                </div>
              )}
            </div>
          )}

          <div className="space-y-1 max-h-96 overflow-y-auto">
            {allEvents.length === 0 && !selectedDoc && (
              <p className="text-sm text-gray-400 text-center py-4">
                Select or search a document to see events
              </p>
            )}
            {allEvents.length === 0 && selectedDoc && (
              <p className="text-sm text-gray-400 text-center py-4">
                Waiting for events...
              </p>
            )}
            {allEvents.map((e, i) => (
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
      </div>
      <ConfirmDeleteDialog
        open={documentToDelete !== null}
        onCancel={() => setDocumentToDelete(null)}
        onConfirm={confirmDeleteDocument}
        isDeleting={deletingDocument}
      />
    </div>
  );
}

// Historical events loaded on mount

// SSE skips reconnect for completed pipelines
