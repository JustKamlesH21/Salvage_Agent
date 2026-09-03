import { useCallback, useEffect, useState } from 'react'
import { CaseView } from './components/CaseView'
import { CommandDeck } from './components/CommandDeck'
import { ExceptionDesk } from './components/ExceptionDesk'
import { HowItWorks } from './components/HowItWorks'
import { StatusBar } from './components/StatusBar'
import { api, type Posture } from './lib/api'

/**
 * Four screens, addressed by hash so a case is linkable and the back button
 * works. Still no router dependency — four routes and a regex is less to go
 * wrong on demo day than a library.
 */
type Route =
  | { name: 'deck' }
  | { name: 'case'; id: string }
  | { name: 'how' }
  | { name: 'exceptions' }

function routeFromHash(): Route {
  const hash = window.location.hash
  const match = hash.match(/^#\/case\/(.+)$/)
  if (match) return { name: 'case', id: match[1] }
  if (hash === '#/how-it-works') return { name: 'how' }
  if (hash === '#/exceptions') return { name: 'exceptions' }
  return { name: 'deck' }
}

export default function App() {
  const [route, setRoute] = useState<Route>(routeFromHash)
  const [posture, setPosture] = useState<Posture | null>(null)

  useEffect(() => {
    const onHashChange = () => setRoute(routeFromHash())
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
        {route.name === 'case' ? (
          <CaseView caseId={route.id} onBack={goHome} />
        ) : route.name === 'how' ? (
          <HowItWorks onBack={goHome} onOpenCase={openCase} />
        ) : route.name === 'exceptions' ? (
          <ExceptionDesk onBack={goHome} onOpenCase={openCase} />
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
