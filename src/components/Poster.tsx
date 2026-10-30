import { useState } from 'react'
import type { Movie } from '../types'

/** 2:3 poster with a typographic fallback if the image fails to load. */
export default function Poster({ movie, className = '' }: { movie: Movie; className?: string }) {
  const [failed, setFailed] = useState(false)
  return (
    <div className="relative aspect-[2/3] w-full overflow-hidden rounded-[inherit] bg-ink-700">
      {!failed ? (
        <img
          src={movie.poster}
          alt={`${movie.title} poster`}
          loading="lazy"
          draggable={false}
          onError={() => setFailed(true)}
          className={`size-full object-cover ${className}`}
        />
      ) : (
        <div className="grid size-full place-items-center bg-gradient-to-b from-ink-600 to-ink-900 p-2 text-center">
          <span className="font-display text-lg leading-none text-gold-300">{movie.title}</span>
        </div>
      )}
      <div className="pointer-events-none absolute inset-0 rounded-[inherit] ring-1 ring-inset ring-white/10" />
    </div>
  )
}
