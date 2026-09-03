import { useCallback, useEffect, useState } from 'react'
import { CaseView } from './components/CaseView'
import { CommandDeck } from './components/CommandDeck'
import { StatusBar } from './components/StatusBar'
import { api, type Posture } from './lib/api'

/**
 * Two screens, addressed by hash so a case is linkable and the back button
 * works. No router dependency — there are two routes, and a library for two
 * routes would be a layer that has not earned its existence.
 */
function caseIdFromHash(): string | null {
  const match = window.location.hash.match(/^#\/case\/(.+)$/)
  return match ? match[1] : null
}

export default function App() {
  const [caseId, setCaseId] = useState<string | null>(caseIdFromHash)
  const [posture, setPosture] = useState<Posture | null>(null)

  useEffect(() => {
    const onHashChange = () => setCaseId(caseIdFromHash())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  useEffect(() => {
    let alive = true
    const load = () =>
      api
        .posture()
        .then((p) => alive && setPosture(p))
        .catch(() => undefined)
    void load()
    const timer = setInterval(() => void load(), 5000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])

  const openCase = useCallback((id: string) => {
    window.location.hash = `#/case/${id}`
  }, [])

  const goHome = useCallback(() => {
    window.location.hash = ''
  }, [])

  const onKill = useCallback((engaged: boolean) => {
    void api.kill(engaged).then(setPosture)
  }, [])

  return (
    <div className="min-h-screen">
      <StatusBar posture={posture} onKill={onKill} onHome={goHome} />
      <main className="mx-auto max-w-6xl">
        {caseId ? (
          <CaseView caseId={caseId} onBack={goHome} />
        ) : (
          <CommandDeck onOpenCase={openCase} />
        )}
      </main>
      <footer className="mx-auto max-w-6xl border-t border-rule px-4 py-4">
        <p className="data text-data-sm leading-relaxed text-dim">
          Timestamps stored UTC, rendered IST. Gates evaluate in IST. Dry run unless the
          bar says otherwise — and live dispatch is not built in this submission.
        </p>
      </footer>
    </div>
  )
}
