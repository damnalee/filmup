const API_BASE_URL = ((import.meta.env.VITE_CINEMA_API_URL as string | undefined)?.trim() || '/api').replace(/\/$/, '')

export interface AdvanceBookingSubscription {
  movie_title: string
  email: string
  cinema_names: string[]
}

export async function requestAdvanceBookingAlert(payload: AdvanceBookingSubscription): Promise<string> {
  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}/advance-booking/subscribe`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(payload),
    })
  } catch {
    throw new Error('FilmUp could not reach the notification service. Please try again later.')
  }

  const result = await response.json().catch(() => ({})) as { message?: string; detail?: string }
  if (!response.ok) {
    throw new Error(result.detail || 'We could not create your notification request. Please try again.')
  }
  return result.message || 'Check your inbox to confirm your email alerts.'
}
