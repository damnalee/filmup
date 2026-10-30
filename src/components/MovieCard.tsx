import { motion } from 'framer-motion'
import { Clock, Languages } from 'lucide-react'
import type { Movie } from '../types'
import { formatDuration, movieLanguageLabel } from '../lib/schedule'
import ShowtimeChips from './ShowtimeChips'
import Poster from './Poster'

interface Props {
  movie: Movie
  /** Resolved language label for this cinema's listing. */
  language?: string
  times: string[]
  date: string
  now: Date
  highlighted?: boolean
  highlightTime?: (time: string) => boolean
  onSelectTime: (time: string) => void
  onMovieClick?: () => void
}

export const listItem = {
  hidden: { opacity: 0, y: 18 },
  show: { opacity: 1, y: 0, transition: { duration: 0.45, ease: [0.22, 1, 0.36, 1] as const } },
}

export default function MovieCard({ movie, language = movieLanguageLabel(movie), times, date, now, highlighted, highlightTime, onSelectTime, onMovieClick }: Props) {
  return (
    <motion.article
      variants={listItem}
      layout
      className={[
        'group relative flex gap-4 rounded-2xl p-3 transition-colors duration-300',
        highlighted
          ? 'border border-gold-400/40 bg-gradient-to-br from-gold-400/[0.09] to-transparent shadow-[0_0_40px_-12px_rgb(245_194_107/0.45)]'
          : 'glass-soft hover:bg-white/[0.06]',
      ].join(' ')}
    >
      <button
        type="button"
        onClick={onMovieClick}
        className="relative w-[76px] shrink-0 self-start overflow-hidden rounded-xl sm:w-[88px]"
        aria-label={`Show all cinemas playing ${movie.title}`}
      >
        <Poster movie={movie} className="transition-transform duration-500 group-hover:scale-105" />
      </button>

      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-2">
          <h3 className="font-display text-[22px] leading-none tracking-wide text-white">{movie.title}</h3>
          {movie.rating && (
            <span className="shrink-0 rounded-md border border-white/15 px-1.5 py-0.5 text-[10px] font-bold text-neutral-300">
              {movie.rating}
            </span>
          )}
        </div>
        {(movie.genre.length > 0 || movie.duration > 0 || language) && (
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] font-medium text-neutral-400">
            {movie.genre.length > 0 && <span className="text-gold-300/90">{movie.genre.join(' · ')}</span>}
            {movie.duration > 0 && <span className="inline-flex items-center gap-1"><Clock className="size-3" />{formatDuration(movie.duration)}</span>}
            {language && <span className="inline-flex items-center gap-1"><Languages className="size-3" />{language}</span>}
          </div>
        )}
        {highlighted && (
          <p className="mt-1 text-[10px] font-bold uppercase tracking-[0.18em] text-gold-400">Matches your search</p>
        )}
        <div className="mt-3">
          <ShowtimeChips date={date} times={times} now={now} highlight={highlightTime} onSelect={onSelectTime} />
        </div>
      </div>
    </motion.article>
  )
}
