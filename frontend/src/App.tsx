import { useEffect, useState } from "react";

type Check = { ok: boolean; error?: string };
type Health = { status: "ok" | "degraded"; checks: Record<string, Check> };

const POLL_MS = 10_000;

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        // A 503 still carries a JSON body describing which dependency is down.
        const res = await fetch("/api/health");
        const body = (await res.json()) as Health;
        if (!cancelled) {
          setHealth(body);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) {
          setHealth(null);
          setError(e instanceof Error ? e.message : String(e));
        }
      }
    }

    load();
    const id = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <main>
      <h1>NYC Transit Assistant</h1>
      <section aria-live="polite">
        <h2>System status</h2>
        {error && <p className="bad">API unreachable: {error}</p>}
        {!error && !health && <p>Checking…</p>}
        {health && (
          <>
            <p className={health.status === "ok" ? "good" : "bad"}>
              Overall: <strong>{health.status}</strong>
            </p>
            <ul>
              {Object.entries(health.checks).map(([name, check]) => (
                <li key={name} className={check.ok ? "good" : "bad"}>
                  {name}: {check.ok ? "ok" : `down (${check.error})`}
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
    </main>
  );
}
