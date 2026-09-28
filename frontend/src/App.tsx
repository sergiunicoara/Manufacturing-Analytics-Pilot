import { useEffect, useState } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export function App() {
  const [apiStatus, setApiStatus] = useState<"checking" | "ok" | "unreachable">("checking");

  useEffect(() => {
    fetch(`${API_BASE_URL}/health`)
      .then((r) => (r.ok ? setApiStatus("ok") : setApiStatus("unreachable")))
      .catch(() => setApiStatus("unreachable"));
  }, []);

  return (
    <div style={{ fontFamily: "system-ui, sans-serif", maxWidth: 720, margin: "80px auto", padding: 24 }}>
      <h1>Manufacturing Analytics Pilot</h1>
      <p>
        Synthetic-data manufacturing analytics pilot (CP1 scaffold). The executive
        dashboard — Plant Overview, Demand &amp; Forecast, Production Flow, BOM
        Explorer, Capacity, WIP &amp; Lead Time, Scenario Lab, Data Quality, and the
        Recommendation view — is built in CP4 per <code>PLAN.md</code>.
      </p>
      <p>
        API health: <strong>{apiStatus}</strong>
      </p>
      <p style={{ color: "#666", fontSize: 14 }}>
        All data in this system is entirely synthetic and fictional. This is not
        connected to any real ERP, MES, or BI system.
      </p>
    </div>
  );
}
