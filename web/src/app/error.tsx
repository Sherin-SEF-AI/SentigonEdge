"use client";

// App Router route-error boundary. Without this, a render-time throw from a single
// malformed record (an incident, a perception frame, an analytics row) propagated to
// the root and React 19 unmounted the ENTIRE console to a blank page — wall, threat
// queue and dispatch all going dark at once. Now it degrades to a recoverable panel.
import { useEffect } from "react";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // surface to the console for triage; a real deployment can wire this to Sentry
    console.error("console error boundary:", error);
  }, [error]);

  return (
    <div
      role="alert"
      style={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: "0.75rem",
        background: "#0b0f14",
        color: "#e5e7eb",
        fontFamily: "ui-sans-serif, system-ui, sans-serif",
        padding: "2rem",
        textAlign: "center",
      }}
    >
      <h2 style={{ fontSize: "1.1rem", fontWeight: 600 }}>Something went wrong in the console.</h2>
      <p style={{ fontSize: "0.85rem", color: "#9ca3af", maxWidth: "40rem" }}>
        {error.message || "An unexpected error occurred while rendering this view."}
        {error.digest ? ` (ref ${error.digest})` : ""}
      </p>
      <button
        onClick={reset}
        style={{
          marginTop: "0.5rem",
          padding: "0.5rem 1rem",
          borderRadius: "0.375rem",
          border: "1px solid #334155",
          background: "#1e293b",
          color: "#e5e7eb",
          cursor: "pointer",
        }}
      >
        Reload this view
      </button>
    </div>
  );
}
