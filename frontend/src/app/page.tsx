"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

interface Patient {
  id: string;
  name: string;
  external_ref: string;
}

export default function HomePage() {
  const [patients, setPatients] = useState<Patient[]>([]);
  const [newName, setNewName] = useState("");
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadPatients();
  }, []);

  async function ensureTenant(): Promise<string> {
    // Try known tenant IDs from localStorage first
    const stored = localStorage.getItem("tenantId");
    if (stored) {
      try {
        await api.getTenant(stored);
        return stored;
      } catch {
        // stored tenant no longer valid
      }
    }
    // Create a new tenant
    const result = await api.createTenant("RVR Healthcare");
    localStorage.setItem("tenantId", result.id);
    return result.id;
  }

  async function loadPatients() {
    try {
      setLoading(true);
      const tid = await ensureTenant();
      setTenantId(tid);
      const data = await api.listPatients(tid);
      setPatients(data);
    } catch (e) {
      console.error("Failed to load patients:", e);
    } finally {
      setLoading(false);
    }
  }

  async function addPatient() {
    if (!newName.trim() || !tenantId) return;
    try {
      await api.createPatient(tenantId, newName.trim());
      setNewName("");
      loadPatients();
    } catch (e) {
      console.error("Failed to create patient:", e);
    }
  }

  return (
    <div>
      <div className="flex justify-between items-center mb-8">
        <div>
          <h2 className="text-2xl font-bold text-gray-900">Patients</h2>
          <p className="text-sm text-gray-500 mt-1">{patients.length} patients</p>
        </div>
        <div className="flex gap-2">
          <input
            type="text"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Patient name"
            className="border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-primary-500 focus:outline-none"
            onKeyDown={(e) => e.key === "Enter" && addPatient()}
          />
          <button
            onClick={addPatient}
            className="bg-primary-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-primary-700"
          >
            Add Patient
          </button>
        </div>
      </div>

      {loading ? (
        <div className="text-center py-12 text-gray-500">Loading...</div>
      ) : patients.length === 0 ? (
        <div className="text-center py-12 bg-white rounded-xl border">
          <p className="text-gray-500">No patients yet. Add one to get started.</p>
        </div>
      ) : (
        <div className="grid gap-4">
          {patients.map((p) => (
            <a
              key={p.id}
              href={`/patients/${p.id}`}
              className="bg-white rounded-xl border p-5 hover:shadow-md transition-shadow flex justify-between items-center"
            >
              <div>
                <h3 className="font-semibold text-gray-900">{p.name}</h3>
                {p.external_ref && (
                  <p className="text-sm text-gray-500">Ref: {p.external_ref}</p>
                )}
              </div>
              <span className="text-primary-600 text-sm font-medium">View &rarr;</span>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
