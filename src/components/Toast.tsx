import { useEffect } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Phone, Ticket, X } from 'lucide-react'

export interface ToastData {
  id: number
  title: string
  subtitle: string
  /** Omitted or empty → no Book button */
  phone?: string
}

export default function Toast({ toast, onDismiss }: { toast: ToastData | null; onDismiss: () => void }) {
  useEffect(() => {
    if (!toast) return
    const t = setTimeout(onDismiss, 6000)
    return () => clearTimeout(t)
  }, [toast, onDismiss])

  return (
    <div className="pointer-events-none fixed inset-x-0 top-4 z-[75] flex justify-center px-4">
      <AnimatePresence mode="wait">
        {toast && (
          <motion.div
            key={toast.id}
            initial={{ opacity: 0, y: -24, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -16, scale: 0.97 }}
            transition={{ type: 'spring', stiffness: 380, damping: 30 }}
            className="glass pointer-events-auto flex w-full max-w-md items-center gap-3 rounded-2xl p-3 pr-2"
            role="status"
          >
            <div className="grid size-10 shrink-0 place-items-center rounded-xl bg-gold-400/15 text-gold-300">
              <Ticket className="size-5" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-bold text-white">{toast.title}</p>
              <p className="truncate text-xs text-neutral-400">{toast.subtitle}</p>
            </div>
            {toast.phone && (
              <a
                href={`tel:${toast.phone.replace(/\s/g, '')}`}
                className="inline-flex shrink-0 items-center gap-1.5 rounded-xl bg-gold-400 px-3 py-2 text-xs font-bold text-ink-950 transition hover:bg-gold-300"
              >
                <Phone className="size-3.5" /> Book
              </a>
            )}
            <button type="button" onClick={onDismiss} className="grid size-8 shrink-0 place-items-center rounded-full text-neutral-500 hover:text-white" aria-label="Dismiss">
              <X className="size-4" />
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
