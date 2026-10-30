import { useMemo, useState } from 'react'
import { BellRing, CheckCircle2, LoaderCircle, Mail, Ticket } from 'lucide-react'
import type { Cinema } from '../types'
import { requestAdvanceBookingAlert } from '../services/advanceBooking'

interface Props {
  movieTitle: string
  cinemas: Cinema[]
}

export default function AdvanceBookingSignup({ movieTitle, cinemas }: Props) {
  const options = useMemo(
    () => [...cinemas].filter((cinema) => cinema.verified).sort((a, b) => a.name.localeCompare(b.name)),
    [cinemas],
  )
  const [email, setEmail] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [success, setSuccess] = useState('')
  const [error, setError] = useState('')

  const toggleCinema = (id: string) => {
    setSelected((current) => current.includes(id)
      ? current.filter((value) => value !== id)
      : [...current, id])
  }

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    setSuccess('')
    if (!selected.length) {
      setError('Choose at least one cinema to watch.')
      return
    }
    setBusy(true)
    try {
      const message = await requestAdvanceBookingAlert({
        movie_title: movieTitle,
        email,
        cinema_names: options.filter((cinema) => selected.includes(cinema.id)).map((cinema) => cinema.name),
      })
      setSuccess(message)
      setEmail('')
      setSelected([])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="mt-4 rounded-2xl border border-gold-300/20 bg-gradient-to-br from-gold-300/[0.09] to-white/[0.02] p-4">
      <div className="flex items-start gap-3">
        <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-gold-300/10 text-gold-300">
          <BellRing className="size-5" />
        </span>
        <div className="min-w-0">
          <p className="text-sm font-extrabold text-white">Get notified when advance booking opens</p>
          <p className="mt-1 text-xs leading-relaxed text-neutral-400">
            Choose the cinemas you care about. We'll email you when booking slots appear — not just when the movie releases.
          </p>
        </div>
      </div>

      {success ? (
        <div className="mt-4 rounded-xl border border-emerald-400/20 bg-emerald-400/[0.07] p-3">
          <div className="flex items-start gap-2 text-emerald-300">
            <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
            <p className="text-sm font-semibold">{success}</p>
          </div>
          <p className="mt-2 text-xs leading-relaxed text-neutral-400">Your alert stays inactive until you click the confirmation link in that email.</p>
        </div>
      ) : (
        <form onSubmit={submit} className="mt-4 space-y-4">
          <label className="block">
            <span className="mb-1.5 block text-[11px] font-bold uppercase tracking-wider text-neutral-400">Your email address</span>
            <span className="flex items-center gap-2 rounded-xl border border-white/10 bg-black/25 px-3 focus-within:border-gold-300/50">
              <Mail className="size-4 shrink-0 text-neutral-500" />
              <input
                type="email"
                required
                maxLength={320}
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="you@example.com"
                className="min-w-0 flex-1 bg-transparent py-3 text-sm text-white outline-none placeholder:text-neutral-600"
              />
            </span>
          </label>

          <fieldset>
            <legend className="mb-2 text-[11px] font-bold uppercase tracking-wider text-neutral-400">Notify me from these cinemas</legend>
            <div className="grid gap-2 sm:grid-cols-2">
              {options.map((cinema) => {
                const checked = selected.includes(cinema.id)
                return (
                  <label key={cinema.id} className={`flex cursor-pointer items-center gap-2.5 rounded-xl border px-3 py-2.5 transition ${checked ? 'border-gold-300/45 bg-gold-300/[0.08]' : 'border-white/[0.08] bg-black/15 hover:border-white/20'}`}>
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleCinema(cinema.id)}
                      className="size-4 accent-amber-300"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-bold text-neutral-200">{cinema.name}</span>
                      <span className="block truncate text-[10px] text-neutral-500">{cinema.area}</span>
                    </span>
                    {checked && <Ticket className="size-3.5 shrink-0 text-gold-300" />}
                  </label>
                )
              })}
            </div>
            {!options.length && <p className="text-xs text-neutral-500">No verified cinemas are currently available.</p>}
          </fieldset>

          {error && <p role="alert" className="rounded-lg border border-red-400/20 bg-red-400/[0.06] px-3 py-2 text-xs text-red-200">{error}</p>}

          <button
            type="submit"
            disabled={busy || !options.length}
            className="flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-b from-gold-300 to-gold-500 px-4 py-3 text-sm font-extrabold text-ink-950 shadow-glow transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {busy ? <LoaderCircle className="size-4 animate-spin" /> : <BellRing className="size-4" />}
            {busy ? 'Sending confirmation…' : 'Notify me when booking opens'}
          </button>
          <p className="text-center text-[10px] leading-relaxed text-neutral-500">
            We'll first send one confirmation email. Booking alerts start only after you confirm. You can ignore the email to cancel the request.
          </p>
        </form>
      )}
    </section>
  )
}
