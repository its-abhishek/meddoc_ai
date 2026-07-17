const API = "http://localhost:8000";
const MONITOR_API = "http://localhost:8001";

async function fetchAPI(url: string, options?: RequestInit, baseUrl?: string) {
  const base = baseUrl || API;
  const headers: Record<string, string> = {};
  if (options?.body && typeof options.body === "string") {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(`${base}${url}`, {
    ...options,
    headers: { ...headers, ...(options?.headers as Record<string, string>) },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API error ${res.status}: ${text}`);
  }
  return res.json();
}

export const api = {
  // Tenants
  createTenant: (name: string) =>
    fetchAPI("/api/tenants", { method: "POST", body: JSON.stringify({ name }) }),
  getTenant: (id: string) => fetchAPI(`/api/tenants/${id}`),
  signup: (tenantName: string, email: string) =>
    fetchAPI("/api/tenants/signup", {
      method: "POST",
      body: JSON.stringify({ tenant_name: tenantName, user_email: email }),
    }),
  getDashboard: (tenantId: string) => fetchAPI(`/api/tenants/${tenantId}/dashboard`),

  // Patients
  createPatient: (tenantId: string, name: string, externalRef?: string) =>
    fetchAPI(`/api/tenants/${tenantId}/patients`, {
      method: "POST",
      body: JSON.stringify({ name, external_ref: externalRef }),
    }),
  listPatients: (tenantId: string) =>
    fetchAPI(`/api/tenants/${tenantId}/patients`),
  getPatient: (tenantId: string, patientId: string) =>
    fetchAPI(`/api/tenants/${tenantId}/patients/${patientId}`),

  // Documents
  uploadDocument: async (tenantId: string, patientId: string, file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch(
      `${API}/api/tenants/${tenantId}/patients/${patientId}/documents`,
      { method: "POST", body: formData }
    );
    if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
    return res.json();
  },
  listDocuments: (tenantId: string, patientId: string) =>
    fetchAPI(`/api/tenants/${tenantId}/patients/${patientId}/documents`),
  deleteDocument: (tenantId: string, documentId: string) =>
    fetchAPI(`/api/tenants/${tenantId}/documents/${documentId}`, { method: "DELETE" }),

  // Structured data
  getLabResults: (tenantId: string, patientId: string) =>
    fetchAPI(`/api/tenants/${tenantId}/patients/${patientId}/lab-results`),
  getPrescriptions: (tenantId: string, patientId: string) =>
    fetchAPI(`/api/tenants/${tenantId}/patients/${patientId}/prescriptions`),
  getClaims: (tenantId: string, patientId: string) =>
    fetchAPI(`/api/tenants/${tenantId}/patients/${patientId}/claims`),

  // Trends
  getTrends: (tenantId: string, patientId: string, testName: string) =>
    fetchAPI(`/api/tenants/${tenantId}/patients/${patientId}/trends/${testName}`),

  // Risk flags
  getRiskFlags: (tenantId: string, patientId: string) =>
    fetchAPI(`/api/tenants/${tenantId}/patients/${patientId}/risk-flags`),
  dismissFlag: (tenantId: string, patientId: string, flagId: string, reason: string) =>
    fetchAPI(`/api/tenants/${tenantId}/patients/${patientId}/risk-flags/${flagId}/action`, {
      method: "POST",
      body: JSON.stringify({ action: "dismiss", reason }),
    }),
  acknowledgeFlag: (tenantId: string, patientId: string, flagId: string) =>
    fetchAPI(`/api/tenants/${tenantId}/patients/${patientId}/risk-flags/${flagId}/action`, {
      method: "POST",
      body: JSON.stringify({ action: "acknowledge" }),
    }),

  // Query & Summary
  query: (tenantId: string, patientId: string, question: string) =>
    fetchAPI(`/api/tenants/${tenantId}/patients/${patientId}/query`, {
      method: "POST",
      body: JSON.stringify({ question }),
    }),
  getSummary: (tenantId: string, patientId: string) =>
    fetchAPI(`/api/tenants/${tenantId}/patients/${patientId}/summary`),

  // Reports
  generateReport: (tenantId: string, patientId: string) =>
    fetchAPI(`/api/tenants/${tenantId}/patients/${patientId}/reports/generate`, {
      method: "POST",
    }),
  getReport: (tenantId: string, reportId: string) =>
    fetchAPI(`/api/tenants/${tenantId}/reports/${reportId}`),
  updateReportBlock: (tenantId: string, reportId: string, blockId: string, content: string) =>
    fetchAPI(`/api/tenants/${tenantId}/reports/${reportId}/blocks/${blockId}`, {
      method: "PATCH",
      body: JSON.stringify({ content }),
    }),
  regenerateBlock: (tenantId: string, reportId: string, blockId: string) =>
    fetchAPI(`/api/tenants/${tenantId}/reports/${reportId}/blocks/${blockId}/regenerate`, {
      method: "POST",
    }),
  addCustomBlock: (tenantId: string, reportId: string, content: string) =>
    fetchAPI(`/api/tenants/${tenantId}/reports/${reportId}/blocks`, {
      method: "POST",
      body: JSON.stringify({ content, block_type: "custom_note" }),
    }),
  finalizeReport: (tenantId: string, reportId: string) =>
    fetchAPI(`/api/tenants/${tenantId}/reports/${reportId}/finalize`, {
      method: "POST",
    }),
  downloadReportPDF: async (tenantId: string, reportId: string) => {
    const res = await fetch(`${API}/api/tenants/${tenantId}/reports/${reportId}/pdf`);
    if (!res.ok) throw new Error(`PDF download failed: ${res.status}`);
    const blob = await res.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `report_${reportId.slice(0, 8)}.pdf`;
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(url);
    document.body.removeChild(a);
  },

  // Export
  exportPatient: (tenantId: string, patientId: string) =>
    fetchAPI(`/api/tenants/${tenantId}/patients/${patientId}/export`),

  // Monitoring (routed to monitoring service on port 8001)
  getDocumentStatus: (documentId: string) =>
    fetchAPI(`/monitor/documents/${documentId}/status`, undefined, MONITOR_API),
  getActiveDocuments: (tenantId: string) =>
    fetchAPI(`/monitor/tenants/${tenantId}/active`, undefined, MONITOR_API),
  getAllDocuments: (tenantId: string) =>
    fetchAPI(`/monitor/tenants/${tenantId}/all-documents`, undefined, MONITOR_API),
  getNotifications: (tenantId: string) =>
    fetchAPI(`/monitor/tenants/${tenantId}/notifications`, undefined, MONITOR_API),
};

// downloadReportPDF handles blob download
