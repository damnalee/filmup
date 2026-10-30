/* ──────────────────────────────────────────────────────────────
   Domain model. These shapes mirror src/data/*.json exactly, so an
   API or admin panel only has to return the same JSON.
   ────────────────────────────────────────────────────────────── */

export interface Cinema {
  id: string
  name: string
  address: string
  city: string
  /** Neighbourhood, e.g. "Gulberg". Used for search by location. */
  area: string
  lat: number
  lng: number
  image: string
  /** 0–5 */
  rating: number
  /** May be empty when no public number is known */
  phone: string
  screens?: number
  /** Location + operating status confirmed — see VERIFY_CINEMAS.md */
  verified: boolean
  /** False when the live API has no catalogue or showtime records for this venue yet. */
  hasRecords?: boolean
  features?: string[]
}

export interface Movie {
  id: string
  title: string
  poster: string
  genre: string[]
  /** Minutes */
  duration: number
  language: string
  /** Source-listed language by cinema ID; the same film can have different language versions. */
  languageByCinema?: Record<string, string>
  /** Age rating, e.g. "U", "PG", "PG-13", "15+", "18+" */
  rating: string
  synopsis: string
  director: string
  cast: string[]
  releaseYear: number
  /** Optional — the Watch Trailer button hides when missing */
  trailerUrl?: string
  /** Live catalogue metadata supplied by the FastAPI backend. */
  listingStatus?: 'now_showing' | 'coming_soon' | 'released_no_sessions' | 'unknown' | string
  releaseDate?: string
  releaseDateLabel?: string
  availabilityMessage?: string
  hasUpcomingShowtimes?: boolean
}

/**
 * API-derived showtimes use actual ISO dates.
 * `times` are 24h "HH:mm" strings.
 */
export interface RawShowtime {
  cinemaId: string
  movieId: string
  date: string
  times: string[]
}

/** A showtime after normalisation — `date` is always ISO. */
export type Showtime = RawShowtime

/** A searchable Lahore neighbourhood — used by "Search by location". */
export interface Area {
  id: string
  name: string
  aliases: string[]
  lat: number
  lng: number
}

export interface RawCinemaData {
  cinemas: Cinema[]
  movies: Movie[]
  showtimes: RawShowtime[]
  areas: Area[]
}

export interface CinemaData {
  cinemas: Cinema[]
  movies: Movie[]
  showtimes: Showtime[]
  areas: Area[]
}

export type TimeOfDay = 'morning' | 'afternoon' | 'evening' | 'night'

export interface Filters {
  /** ISO date */
  date: string
  /** Free-text search across movie titles */
  query: string
  movieId: string | null
  genre: string | null
  cinemaId: string | null
  timesOfDay: TimeOfDay[]
}

export interface LatLng {
  lat: number
  lng: number
}

export interface MovieShowing {
  movie: Movie
  times: string[]
}

export interface CinemaSchedule {
  cinema: Cinema
  showings: MovieShowing[]
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}
