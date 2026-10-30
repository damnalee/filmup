import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { motion } from 'framer-motion'
import { Volume2, VolumeX, ChevronRight } from 'lucide-react'

/* Opening sequence.

   The <video> is created by a tiny inline script in index.html, so it starts playing
   before the JS bundle has even loaded. This component adopts that element: it adds the
   sound/skip controls, watches for the end, and falls back to the title card if the
   video errors, autoplay is refused, or nothing has played after 4 s.

   Nothing else renders on top of the video while it plays — no blur, grain or gradients —
   and the map isn't created until the intro has finished (see App). */

const SEEN_KEY = 'filmup:intro-seen'
const START_TIMEOUT_MS = 4000
const TITLE_MS = 1900
const FADE_MS = 500

interface Props {
  /** Fires once frames are moving — a good moment to fetch (not create) the map in the background */
  onStart?: () => void
  /** Fires as the intro starts fading out. `played` is false when it was skipped for this session. */
  onFinish: (played: boolean) => void
}

export default function Intro({ onStart, onFinish }: Props) {
  const [root, setRoot] = useState<HTMLElement | null>(null)
  const [phase, setPhase] = useState<'video' | 'title' | 'done'>('video')
  const [started, setStarted] = useState(false)
  const [muted, setMuted] = useState(true)
  const [hasAudio, setHasAudio] = useState(false)
  const finished = useRef(false)
  const startedRef = useRef(false)

  const finish = useCallback(() => {
    if (finished.current) return
    finished.current = true
    try {
      sessionStorage.setItem(SEEN_KEY, '1')
    } catch {
      /* storage unavailable: the intro just shows again next time */
    }
    const el = document.getElementById('intro')
    el?.querySelector('video')?.pause()
    el?.classList.add('is-leaving') // CSS opacity fade (0.5s, compositor-only)
    onFinish(true)
    setTimeout(() => {
      el?.remove()
      setPhase('done')
    }, FADE_MS + 50)
  }, [onFinish])

  const fallback = useCallback(() => {
    if (finished.current || startedRef.current) return
    const v = document.getElementById('intro-video') as HTMLVideoElement | null
    v?.pause()
    v?.remove()
    setPhase('title')
  }, [])

  useEffect(() => {
    const el = document.getElementById('intro')
    if (!el) {
      onFinish(false) // seen this session (or reduced motion): straight to the map
      return
    }
    el.dataset.adopted = '1'
    setRoot(el)
    const v = el.querySelector('video')
    if (!v) return void setPhase('title')

    const start = () => {
      if (startedRef.current) return
      startedRef.current = true
      setStarted(true)
      onStart?.()
    }
    const onError = () => fallback()

    if (v.error) fallback()
    else if (v.ended) finish()
    else if (!v.paused && v.currentTime > 0) start()
    v.addEventListener('playing', start)
    v.addEventListener('ended', finish)
    v.addEventListener('error', onError)
    v.play().catch(() => !startedRef.current && fallback())

    // Measured from navigation start, since the video may have been loading before React mounted
    const slow = setTimeout(() => !startedRef.current && fallback(), Math.max(0, START_TIMEOUT_MS - performance.now()))

    // Sound toggle only when the encode reported real audio (low-priority, non-blocking)
    fetch('/intro/manifest.json', { priority: 'low' } as RequestInit)
      .then((r) => (r.ok ? r.json() : null))
      .then((m: { hasAudio?: boolean } | null) => setHasAudio(!!m?.hasAudio))
      .catch(() => {})

    return () => {
      clearTimeout(slow)
      v.removeEventListener('playing', start)
      v.removeEventListener('ended', finish)
      v.removeEventListener('error', onError)
    }
  }, [finish, fallback, onFinish, onStart])

  // Title card runs for a fixed time, then hands over like the video does
  useEffect(() => {
    if (phase !== 'title') return
    const t = setTimeout(finish, TITLE_MS)
    return () => clearTimeout(t)
  }, [phase, finish])

  const toggleSound = () => {
    const v = document.getElementById('intro-video') as HTMLVideoElement | null
    if (!v) return
    v.muted = !v.muted
    if (!v.muted && v.volume < 0.6) v.volume = 0.8
    setMuted(v.muted)
  }

  if (!root || phase === 'done') return null

  return createPortal(
    phase === 'title' ? (
      <TitleCard />
    ) : (
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: started ? 1 : 0 }}
        transition={{ delay: 0.4, duration: 0.5 }}
        className="absolute inset-x-0 bottom-0 flex items-end justify-between gap-3 p-4 pb-[max(1rem,env(safe-area-inset-bottom))] sm:p-6"
      >
        {hasAudio ? (
          <button
            type="button"
            onClick={toggleSound}
            className="inline-flex h-10 items-center gap-2 rounded-full border border-white/15 bg-black/55 px-4 text-xs font-semibold text-white/90 transition hover:border-gold-400/50 hover:text-white active:scale-95"
            aria-pressed={!muted}
          >
            {muted ? <VolumeX className="size-4" /> : <Volume2 className="size-4 text-gold-300" />}
            {muted ? 'Tap for sound' : 'Sound on'}
          </button>
        ) : (
          <span />
        )}
        <button
          type="button"
          onClick={finish}
          className="inline-flex h-10 items-center gap-1 rounded-full border border-white/15 bg-black/55 pr-3 pl-4 text-xs font-semibold text-white/80 transition hover:border-white/35 hover:text-white active:scale-95"
        >
          Skip intro
          <ChevronRight className="size-4" />
        </button>
      </motion.div>
    ),
    root,
  )
}

/** The original title card — used when the video can't play. */
function TitleCard() {
  return (
    <div className="absolute inset-0 grid place-items-center bg-ink-950">
      <motion.div
        className="absolute top-1/2 left-1/2 h-[140vh] w-[60vh] -translate-x-1/2 -translate-y-1/2 bg-[conic-gradient(from_180deg_at_50%_0%,transparent_160deg,rgb(245_194_107/0.18)_180deg,transparent_200deg)]"
        initial={{ opacity: 0, scaleY: 0.4 }}
        animate={{ opacity: 1, scaleY: 1 }}
        transition={{ duration: 1.2, ease: [0.22, 1, 0.36, 1] }}
        style={{ transformOrigin: '50% 0%' }}
      />
      <div className="relative text-center">
        <h1 className="wordmark flex text-6xl sm:text-8xl" aria-label="FilmUp">
          {'FilmUp'.split('').map((ch, i) => (
            <motion.span
              key={i}
              className="wordmark-letter"
              initial={{ opacity: 0, y: 40 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.15 + i * 0.06, duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
            >
              {ch}
            </motion.span>
          ))}
        </h1>
        <motion.div
          className="mx-auto mt-3 h-px bg-gradient-to-r from-transparent via-gold-400 to-transparent"
          initial={{ width: 0 }}
          animate={{ width: '100%' }}
          transition={{ delay: 0.6, duration: 0.8, ease: [0.22, 1, 0.36, 1] }}
        />
        <motion.p
          className="mt-3 text-[11px] font-semibold uppercase tracking-[0.5em] text-neutral-400"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.9 }}
        >
          Lahore · Now Showing
        </motion.p>
      </div>
    </div>
  )
}
