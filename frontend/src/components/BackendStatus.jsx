import { useBackendStatus } from '../hooks/useBackendStatus.js'

const labels = {
  checking: 'Checking backend…',
  connected: 'Backend Connected',
  disconnected: 'Backend Disconnected',
}

export default function BackendStatus() {
  const status = useBackendStatus()
  return (
    <div className={`backend-status ${status}`} role="status" aria-live="polite" title={labels[status]}>
      <span className="backend-status-dot" aria-hidden="true" />
      <span>{labels[status]}</span>
    </div>
  )
}
