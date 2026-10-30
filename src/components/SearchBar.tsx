import { useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Search, X, Building2, Clapperboard } from 'lucide-react'

export type SearchMode = 'movie' | 'location'

interface Props {
  /** null = no tab picked: the panel shows all cinemas and the input filters them */
  mode: SearchMode | null
  onModeChange: (mode: SearchMode | null) => void
  value: string
  onChange: (text: string) => void
  onClear: () => void
  /** Arrow keys / Enter / Escape drive the cursor in the list panel */
  onNavigate: (key: 'up' | 'down' | 'enter' | 'escape') => void
  onFocusChange?: (focused: boolean) => void
}

const PLACEHOLDER: Record<'movie' | 'location' | 'none', string> = {
  movie: 'Search a movie…',
  location: 'Search cinemas…',
  none: 'Search cinemas…',
}

/** Movie / Cinemas toggle + input. Results render in the list panel, not in a dropdown. */
export default function SearchBar({ mode, onModeChange, value, onChange, onClear, onNavigate, onFocusChange }: Props) {
  const [focused, setFocused] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  return (
    <div className="relative min-w-0 flex-1">
      <div className="flex min-w-0 items-center gap-2">
        <div className="relative flex shrink-0 rounded-xl bg-white/[0.04] p-0.5" role="radiogroup" aria-label="Browse by">
          {(['movie', 'location'] as const).map((m) => (
            <button
              key={m}
              type="button"
              role="radio"
              aria-checked={mode === m}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => {
                // Clicking the active tab again returns to the cinema list
                onModeChange(mode === m ? null : m)
                inputRef.current?.focus()
              }}
              className={`relative inline-flex h-8 items-center gap-1.5 rounded-[10px] px-2.5 text-xs font-bold transition-colors ${
                mode === m ? 'text-ink-950' : 'text-neutral-400 hover:text-white'
              }`}
              title={mode === m ? 'Back to all cinemas' : m === 'movie' ? 'Browse movies' : 'Browse cinemas'}
            >
              {mode === m && (
                <motion.span layoutId="search-mode" className="absolute inset-0 rounded-[10px] bg-gradient-to-b from-gold-300 to-gold-500" transition={{ type: 'spring', stiffness: 500, damping: 36 }} />
              )}
              {m === 'movie' ? <Clapperboard className="relative size-3.5" /> : <Building2 className="relative size-3.5" />}
              <span className="relative hidden md:inline">{m === 'movie' ? 'Movie' : 'Cinemas'}</span>
            </button>
          ))}
        </div>

        <Search className={`size-4 shrink-0 transition-colors ${focused ? 'text-gold-400' : 'text-neutral-500'}`} />
        <input
          ref={inputRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onFocus={() => {
            setFocused(true)
            onFocusChange?.(true)
          }}
          onBlur={() => {
            setFocused(false)
            onFocusChange?.(false)
          }}
          onKeyDown={(e) => {
            const key = e.key === 'ArrowDown' ? 'down' : e.key === 'ArrowUp' ? 'up' : e.key === 'Enter' ? 'enter' : e.key === 'Escape' ? 'escape' : null
            if (!key) return
            e.preventDefault()
            onNavigate(key)
            if (key === 'escape') inputRef.current?.blur()
          }}
          placeholder={PLACEHOLDER[mode ?? 'none']}
          className="h-10 min-w-0 flex-1 bg-transparent text-sm font-medium text-white placeholder:text-neutral-500 focus:outline-none"
          aria-label={PLACEHOLDER[mode ?? 'none'].replace('…', '')}
          aria-controls="browse-list"
        />
        <AnimatePresence>
          {value && (
            <motion.button
              initial={{ scale: 0.6, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.6, opacity: 0 }}
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={onClear}
              className="grid size-7 shrink-0 place-items-center rounded-full bg-white/8 text-neutral-300 hover:bg-white/15 hover:text-white"
              aria-label="Clear search"
            >
              <X className="size-3.5" />
            </motion.button>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}
