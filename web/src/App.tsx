import { useEffect, useState } from 'react'

/** Safety posture reported by GET /health. Which mode you are in is never a guess. */
type Health = {
  status: string
  app_env: string
  dry_run: boolean
  kill_switch: boolean
  storage_tz: string
  compliance_tz: string
}

/** Day 0 placeholder. The Command Deck (S1) and Decision Tape (S2) land on day 9. */
export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/health')
      .then((r) => (r.ok ? (r.json() as Promise<Health>) : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setHealth)
      .catch((e: Error) => setError(e.message))
  }, [])

  const armed = health !== null && !health.dry_run

  return (
    <div className="min-h-screen">
      <header
        className={`flex flex-wrap items-center gap-4 border-b px-4 py-3 ${
          armed ? 'bg-stamp text-paper' : 'border-rule bg-paper text-ink'
        }`}
      >
        <span className="font-display text-lg font-semibold tracking-tight">SALVAGE</span>
        <span className="data text-xs uppercase">
          {health === null ? '⬤ api unreachable' : armed ? '⬤ ARMED' : '⬤ DRY RUN'}
        </span>
        <span className="data ml-auto text-xs text-dim">
          {health?.compliance_tz ?? 'Asia/Kolkata'} · gates evaluated IST
        </span>
      </header>

      <main className="mx-auto max-w-3xl px-4 py-10">
        <h1 className="font-display text-2xl">Scaffolding</h1>
        <p className="mt-2 max-w-prose text-sm">
          The Command Deck lands on day 9. Until then this page proves one thing: the frontend can
          read the API's safety posture, and the chrome changes colour when the system is armed.
        </p>

        {error !== null && (
          <p className="data mt-6 border-l-2 border-amber pl-3 text-sm">
            API unreachable ({error}). Start it with <code>make api</code>, then reload.
          </p>
        )}

        {health !== null && (
          <dl className="data mt-6 grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm">
            {Object.entries(health).map(([key, value]) => (
              <div key={key} className="contents">
                <dt className="text-dim">{key}</dt>
                <dd>{String(value)}</dd>
              </div>
            ))}
          </dl>
        )}
      </main>
    </div>
  )
}
