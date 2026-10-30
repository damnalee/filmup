import { useCallback, useState } from 'react'
import type { LatLng } from '../types'

type Status = 'idle' | 'locating' | 'ready' | 'error'

export function useGeolocation() {
  const [location, setLocation] = useState<LatLng | null>(null)
  const [status, setStatus] = useState<Status>('idle')
  const [error, setError] = useState<string | null>(null)

  const locate = useCallback(() => {
    if (!('geolocation' in navigator)) {
      setStatus('error')
      setError('Location is not supported by this browser.')
      return
    }
    setStatus('locating')
    setError(null)
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocation({ lat: pos.coords.latitude, lng: pos.coords.longitude })
        setStatus('ready')
      },
      (err) => {
        setStatus('error')
        setError(err.code === err.PERMISSION_DENIED ? 'Location permission was denied.' : 'Could not find your location.')
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
    )
  }, [])

  const clear = useCallback(() => {
    setLocation(null)
    setStatus('idle')
  }, [])

  return { location, status, error, locate, clear }
}
