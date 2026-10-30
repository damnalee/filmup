import { useEffect, useState } from 'react'
import type { CinemaData } from '../types'
import { dataSource } from '../services/dataSource'
import { normalizeData, toISODate } from '../lib/schedule'

type State =
  | { status: 'loading'; data: null; error: null }
  | { status: 'ready'; data: CinemaData; error: null }
  | { status: 'error'; data: null; error: string }

export function useCinemaData(): State {
  const [state, setState] = useState<State>({ status: 'loading', data: null, error: null })

  useEffect(() => {
    let cancelled = false
    dataSource
      .load()
      .then((raw) => {
        if (!cancelled) setState({ status: 'ready', data: normalizeData(raw, toISODate(new Date())), error: null })
      })
      .catch((err: Error) => {
        if (!cancelled) setState({ status: 'error', data: null, error: err.message })
      })
    return () => {
      cancelled = true
    }
  }, [])

  return state
}
