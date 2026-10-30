/**
 * Lahore cinema-day rules for the website UI.
 * Cinema dates roll over at 06:00, so screenings from 00:00 through 05:59
 * belong to the previous cinema schedule date. Past-state checks use the
 * actual calendar date of the screening, not its display/grouping date.
 */
import { addDays, toISODate, toMinutes } from './schedule'

export const CINEMA_DAY_CUTOFF_MINUTES = 6 * 60

/** Return the cinema schedule date currently in progress. */
export function toCinemaDate(now: Date): string {
  const calendarDate = toISODate(now)
  const minuteOfDay = now.getHours() * 60 + now.getMinutes()
  return minuteOfDay < CINEMA_DAY_CUTOFF_MINUTES
    ? addDays(calendarDate, -1)
    : calendarDate
}

/** Convert a session's real local calendar date to its cinema schedule date. */
export function cinemaScheduleDate(actualDate: string, time: string): string {
  return toMinutes(time) < CINEMA_DAY_CUTOFF_MINUTES
    ? addDays(actualDate, -1)
    : actualDate
}

/** Determine past/future using the screening's actual date and local clock time. */
export function isPastCinemaShow(scheduleDate: string, time: string, now: Date): boolean {
  const actualDate = toMinutes(time) < CINEMA_DAY_CUTOFF_MINUTES
    ? addDays(scheduleDate, 1)
    : scheduleDate
  const calendarToday = toISODate(now)

  if (actualDate !== calendarToday) return actualDate < calendarToday
  return toMinutes(time) <= now.getHours() * 60 + now.getMinutes()
}
