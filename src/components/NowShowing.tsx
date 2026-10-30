import { useRef } from 'react'
import { motion } from 'framer-motion'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import type { Movie } from '../types'

interface Props {
  /** Films with at least one confirmed future session anywhere in the API response. */
  movies: Movie[]
  /** Announced future releases with no sessions posted yet. */
  comingSoonMovies: Movie[]
  /** Number of sessions at cinemas for the selected date. */
  cinemaCount: Map<string, number>
  selectedId: string | null
  onSelect: (id: string | null) => void
  revealed: boolean
}

const rowVariants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.025, delayChildren: 0.35 } },
}
const pillVariants = {
  hidden: { opacity: 0, y: 8 },
  show: { opacity: 1, y: 0, transition: { duration: 0.3, ease: [0.22, 1, 0.36, 1] as const } },
}

function RibbonRow({
  label,
  movies,
  kind,
  cinemaCount,
  selectedId,
  onSelect,
  revealed,
}: {
  label: string
  movies: Movie[]
  kind: 'playing' | 'upcoming'
  cinemaCount: Map<string, number>
  selectedId: string | null
  onSelect: (id: string | null) => void
  revealed: boolean
}) {
  const scroller = useRef<HTMLDivElement>(null)
  const scroll = (direction: -1 | 1) => {
    scroller.current?.scrollBy({ left: direction * scroller.current.clientWidth * 0.72, behavior: 'smooth' })
  }
  return (
    <div className="glass pointer-events-auto flex h-14 min-w-0 items-center gap-2 rounded-2xl px-2 sm:px-3" aria-label={label}>
      <div className="w-[76px] shrink-0 border-r border-white/[0.08] pr-2 sm:w-[112px]">
        <p className="text-[9px] font-extrabold uppercase tracking-[0.13em] text-gold-300 sm:text-[10px]">{label}</p>
        <p className="mt-0.5 text-[9px] font-medium text-neutral-500">{movies.length} films</p>
      </div>
      <button type="button" onClick={() => scroll(-1)} className="strip-arrow hidden sm:grid" aria-label={`Scroll ${label} left`}>
        <ChevronLeft className="size-4" />
      </button>
      <motion.div
        ref={scroller}
        variants={rowVariants}
        initial="hidden"
        animate={revealed ? 'show' : 'hidden'}
        className="no-scrollbar flex min-w-0 flex-1 snap-x items-center gap-2 overflow-x-auto scroll-smooth py-1 [mask-image:linear-gradient(90deg,transparent,#000_10px,#000_calc(100%-12px),transparent)]"
      >
        {movies.length ? movies.map((movie) => {
          const selected = movie.id === selectedId
          const count = cinemaCount.get(movie.id) ?? 0
          const subtitle = kind === 'playing'
            ? (count ? `${count} slots today` : 'Upcoming slots available')
            : (movie.releaseDateLabel ? `Release ${movie.releaseDateLabel}` : 'Showtimes not posted')
          return (
            <motion.button
              key={`${kind}-${movie.id}`}
              type="button"
              variants={pillVariants}
              whileTap={{ scale: 0.96 }}
              onClick={() => onSelect(selected ? null : movie.id)}
              aria-pressed={selected}
              title={`${movie.title} — ${subtitle}`}
              className={`group flex h-11 shrink-0 snap-start items-center gap-2 rounded-xl border py-1 pr-3 pl-1 transition-all duration-200 ${
                selected
                  ? 'border-gold-400/60 bg-gold-400/15 shadow-[0_0_24px_-6px_rgb(245_194_107/0.6)]'
                  : 'border-white/[0.06] bg-white/[0.03] hover:border-white/15 hover:bg-white/[0.07]'
              }`}
            >
              <img src={movie.poster || '/posters/no-poster.svg'} alt="" loading="lazy" draggable={false} className="h-9 w-[25px] shrink-0 rounded-md object-cover ring-1 ring-white/10" />
              <span className="min-w-0 text-left">
                <span className={`block max-w-[130px] truncate text-[11px] font-bold ${selected ? 'text-gold-100' : 'text-neutral-100'}`}>{movie.title}</span>
                <span className="block max-w-[160px] truncate text-[9px] font-medium text-neutral-500">{subtitle}</span>
              </span>
            </motion.button>
          )
        }) : (
          <p className="px-2 text-[10px] font-medium text-neutral-500">
            {kind === 'playing' ? 'No movies have confirmed upcoming slots' : 'No upcoming movies listed yet'}
          </p>
        )}
      </motion.div>
      <button type="button" onClick={() => scroll(1)} className="strip-arrow hidden sm:grid" aria-label={`Scroll ${label} right`}>
        <ChevronRight className="size-4" />
      </button>
    </div>
  )
}

/** Two bottom ribbons: confirmed showtimes first, upcoming catalogue-only titles second. */
export default function NowShowing({ movies, comingSoonMovies, cinemaCount, selectedId, onSelect, revealed }: Props) {
  return (
    <section className="pointer-events-auto flex flex-col gap-2" aria-label="Movie ribbons">
      <RibbonRow label="Now Showing" movies={movies} kind="playing" cinemaCount={cinemaCount} selectedId={selectedId} onSelect={onSelect} revealed={revealed} />
      <RibbonRow label="Upcoming" movies={comingSoonMovies} kind="upcoming" cinemaCount={cinemaCount} selectedId={selectedId} onSelect={onSelect} revealed={revealed} />
    </section>
  )
}
