import { useEffect, useState } from 'react'
import { getHealth } from '../services/api.js'

export function useBackendStatus(pollInterval = 15000) {
  const [status, setStatus] = useState('checking')

  useEffect(() => {
    let mounted = true
    let controller

    const checkHealth = async () => {
      controller?.abort()
      const requestController = new AbortController()
      controller = requestController
      try {
        await getHealth({ signal: requestController.signal })
        if (mounted) setStatus('connected')
      } catch {
        if (mounted && !requestController.signal.aborted) setStatus('disconnected')
      }
    }

    checkHealth()
    const intervalId = window.setInterval(checkHealth, pollInterval)
    return () => {
      mounted = false
      controller?.abort()
      window.clearInterval(intervalId)
    }
  }, [pollInterval])

  return status
}
