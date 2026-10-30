import { motion } from 'framer-motion'
import { LocateFixed, Loader2 } from 'lucide-react'

interface Props {
  status: 'idle' | 'locating' | 'ready' | 'error'
  onLocate: () => void
  onClear: () => void
}

/** Sits at the end of the search bar: icon-only on small screens, labelled on wide ones. */
export default function NearMeButton({ status, onLocate, onClear }: Props) {
  const ready = status === 'ready'
  return (
    <motion.button
      type="button"
      whileTap={{ scale: 0.94 }}
      onClick={ready ? onClear : onLocate}
      disabled={status === 'locating'}
      aria-pressed={ready}
      aria-label={ready ? 'Stop using my location' : 'Near me — sort cinemas by distance'}
      title={ready ? 'Stop using my location' : 'Sort cinemas by distance'}
      className={`inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-xl px-3 text-xs font-bold transition-all duration-200 ${
        ready
          ? 'bg-sky-300/15 text-sky-200 ring-1 ring-sky-300/40'
          : 'bg-white/[0.05] text-neutral-200 ring-1 ring-white/[0.08] hover:bg-white/[0.09] hover:text-gold-200'
      }`}
    >
      {status === 'locating' ? <Loader2 className="size-4 animate-spin" /> : <LocateFixed className="size-4" />}
      <span className="hidden xl:inline">{status === 'locating' ? 'Locating…' : 'Near me'}</span>
    </motion.button>
  )
}
