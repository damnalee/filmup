import { motion } from 'framer-motion'
import type { DateOption } from '../lib/schedule'

interface Props {
  options: DateOption[]
  value: string
  onChange: (iso: string) => void
  layoutId?: string
}

export default function DateSelector({ options, value, onChange, layoutId = 'date-pill' }: Props) {
  return (
    <div className="no-scrollbar -mx-1 flex gap-1.5 overflow-x-auto px-1 py-1" role="tablist" aria-label="Choose a date">
      {options.map((o) => {
        const active = o.iso === value
        return (
          <button
            key={o.iso}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(o.iso)}
            className={`relative flex min-w-[58px] shrink-0 flex-col items-center rounded-xl px-3 py-2 transition-colors ${
              active ? 'text-ink-950' : 'text-neutral-400 hover:bg-white/5 hover:text-white'
            }`}
          >
            {active && (
              <motion.span
                layoutId={layoutId}
                className="absolute inset-0 rounded-xl bg-gradient-to-b from-gold-300 to-gold-500 shadow-glow"
                transition={{ type: 'spring', stiffness: 420, damping: 34 }}
              />
            )}
            <span className="relative text-[10px] font-bold uppercase tracking-[0.14em]">{o.label}</span>
            <span className="relative font-display text-xl leading-none">{o.day}</span>
          </button>
        )
      })}
    </div>
  )
}
