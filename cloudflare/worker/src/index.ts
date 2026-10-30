interface Env {
  SUPABASE_URL: string;
  SUPABASE_KEY: string;
  GITHUB_ACTIONS_TOKEN: string;
  GITHUB_REPOSITORY?: string;
  GITHUB_BRANCH?: string;
  ADMIN_MONITORING_TOKEN?: string;
  CORS_ALLOWED_ORIGINS?: string;
  RESEND_API_KEY?: string;
  MAIL_FROM_EMAIL?: string;
  MAIL_FROM_NAME?: string;
}

interface ScheduledController {
  cron: string;
  scheduledTime: number;
}

interface ExecutionContext {
  waitUntil(promise: Promise<unknown>): void;
}

const CATALOG_FIELDS = [
  'id', 'source', 'cinema_name', 'source_movie_id', 'movie_title',
  'listing_status', 'release_date', 'source_release_label', 'poster_url',
  'detail_url', 'trailer_url', 'language', 'movie_format', 'last_seen_at',
].join(',');

const SHOWTIME_FIELDS = 'movie_title,cinema_name,showtime,booking_link,ticket_price';
const DEFAULT_ORIGINS = ['https://filmup.pages.dev'];

function allowedOrigins(env: Env): Set<string> {
  const configured = (env.CORS_ALLOWED_ORIGINS ?? '')
    .split(',')
    .map((value) => value.trim().replace(/\/$/, ''))
    .filter(Boolean);
  return new Set(configured.length ? configured : DEFAULT_ORIGINS);
}

function corsHeaders(origin: string | null, env: Env): Headers {
  const headers = new Headers();
  if (origin && allowedOrigins(env).has(origin.replace(/\/$/, ''))) {
    headers.set('Access-Control-Allow-Origin', origin);
    headers.set('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    headers.set('Access-Control-Allow-Headers', 'Accept, Content-Type, Authorization');
    headers.set('Access-Control-Max-Age', '86400');
    headers.append('Vary', 'Origin');
  }
  return headers;
}

function json(data: unknown, status = 200, headers = new Headers()): Response {
  const responseHeaders = new Headers(headers);
  responseHeaders.set('Content-Type', 'application/json; charset=utf-8');
  responseHeaders.set('Cache-Control', 'no-store');
  return new Response(JSON.stringify(data), { status, headers: responseHeaders });
}

function supabaseRestUrl(env: Env, table: string, params: URLSearchParams): string {
  const base = env.SUPABASE_URL?.replace(/\/$/, '');
  if (!base || !env.SUPABASE_KEY) throw new Error('Supabase Worker secrets are not configured.');
  return base + '/rest/v1/' + table + '?' + params.toString();
}

async function proxySupabase(
  env: Env,
  table: string,
  params: URLSearchParams,
  origin: string | null,
): Promise<Response> {
  let upstream: Response;
  try {
    upstream = await fetch(supabaseRestUrl(env, table, params), {
      method: 'GET',
      headers: {
        apikey: env.SUPABASE_KEY,
        Authorization: 'Bearer ' + env.SUPABASE_KEY,
        Accept: 'application/json',
      },
    });
  } catch (error) {
    console.error('Supabase request failed for ' + table, error);
    return json({ detail: 'The FilmUp database could not be queried.' }, 502, corsHeaders(origin, env));
  }

  const headers = new Headers(upstream.headers);
  for (const [key, value] of corsHeaders(origin, env)) headers.set(key, value);
  headers.set('Cache-Control', 'no-store');
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers,
  });
}


async function supabaseJson(
  env: Env,
  table: string,
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE',
  params: URLSearchParams,
  body?: unknown,
): Promise<unknown> {
  const headers: Record<string, string> = {
    apikey: env.SUPABASE_KEY,
    Authorization: 'Bearer ' + env.SUPABASE_KEY,
    Accept: 'application/json',
  };
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    headers.Prefer = 'return=representation';
  }
  const response = await fetch(supabaseRestUrl(env, table, params), {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  if (!response.ok) {
    console.error('Supabase write/read failed for ' + table + ' (HTTP ' + response.status + ').');
    throw new Error('The FilmUp database request failed.');
  }
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    throw new Error('The FilmUp database returned an invalid response.');
  }
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function createConfirmationToken(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
}

async function sendResendEmail(
  env: Env,
  recipient: string,
  subject: string,
  plainText: string,
  htmlContent: string,
): Promise<boolean> {
  if (!env.RESEND_API_KEY || !env.MAIL_FROM_EMAIL) return false;
  let response: Response;
  try {
    response = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: {
        Authorization: 'Bearer ' + env.RESEND_API_KEY,
        accept: 'application/json',
        'content-type': 'application/json',
      },
      body: JSON.stringify({
        from: (env.MAIL_FROM_NAME || 'FilmUp Notifications') + ' <' + env.MAIL_FROM_EMAIL + '>',
        to: [recipient],
        subject,
        text: plainText,
        html: htmlContent,
      }),
    });
  } catch (error) {
    console.error('Resend request could not be completed:', error instanceof Error ? error.name : 'unknown error');
    return false;
  }
  if (!response.ok) {
    // Do not log API keys, recipient details, request payloads or upstream response bodies.
    console.error('Resend rejected a FilmUp transactional email (HTTP ' + response.status + ').');
    return false;
  }
  return true;
}

function confirmationPage(title: string, message: string, success = true): Response {
  const safeTitle = escapeHtml(title);
  const safeMessage = escapeHtml(message);
  const accent = success ? '#f5c26b' : '#ff8b8b';
  return new Response(
    '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<title>' + safeTitle + ' · FilmUp</title><body style="margin:0;min-height:100vh;display:grid;place-items:center;background:#09090d;color:#f5f5f5;font:16px/1.6 Arial,sans-serif">' +
    '<main style="max-width:520px;margin:24px;padding:36px;border:1px solid #ffffff18;border-radius:22px;background:#14141b;text-align:center">' +
    '<div style="font-size:13px;letter-spacing:.2em;color:' + accent + ';font-weight:700">FILMUP</div>' +
    '<h1 style="font-size:30px;margin:14px 0 8px">' + safeTitle + '</h1>' +
    '<p style="color:#c7c7d1">' + safeMessage + '</p>' +
    '<p style="margin-top:28px;color:#858592;font-size:13px">You can close this page and return to FilmUp.</p>' +
    '</main></body></html>',
    { status: 200, headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' } },
  );
}

async function subscribeForAdvanceBooking(request: Request, env: Env, origin: string | null): Promise<Response> {
  const cors = corsHeaders(origin, env);
  if (!env.RESEND_API_KEY || !env.MAIL_FROM_EMAIL) {
    return json({ detail: 'Advance-booking email delivery is not configured yet.' }, 503, cors);
  }
  if (!env.SUPABASE_URL || !env.SUPABASE_KEY) {
    return json({ detail: 'The notification database is not configured.' }, 503, cors);
  }

  let payload: { movie_title?: unknown; email?: unknown; cinema_names?: unknown };
  try {
    payload = await request.json() as typeof payload;
  } catch {
    return json({ detail: 'The subscription request must contain valid JSON.' }, 400, cors);
  }

  const movieTitle = typeof payload.movie_title === 'string' ? payload.movie_title.trim().replace(/\s+/g, ' ') : '';
  const email = typeof payload.email === 'string' ? payload.email.trim().toLowerCase() : '';
  const rawCinemas = Array.isArray(payload.cinema_names) ? payload.cinema_names : [];
  const cinemaNames = [...new Set(rawCinemas
    .filter((value): value is string => typeof value === 'string')
    .map((value) => value.trim().replace(/\s+/g, ' '))
    .filter(Boolean))];

  if (!movieTitle || movieTitle.length > 200) {
    return json({ detail: 'A movie title is required (maximum 200 characters).' }, 422, cors);
  }
  if (email.length > 320 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return json({ detail: 'Enter a valid email address.' }, 422, cors);
  }
  if (!cinemaNames.length || cinemaNames.length > 20 || cinemaNames.some((name) => name.length > 120)) {
    return json({ detail: 'Select between 1 and 20 valid cinemas.' }, 422, cors);
  }

  const token = createConfirmationToken();
  const now = new Date();
  const expiresAt = new Date(now.getTime() + 48 * 60 * 60 * 1000).toISOString();
  const record = {
    email,
    movie_title: movieTitle,
    cinema_names: cinemaNames,
    notified_cinemas: [],
    confirmation_token: token,
    status: 'pending',
    expires_at: expiresAt,
  };

  let subscriptionId = '';
  try {
    const result = await supabaseJson(
      env,
      'advance_booking_subscriptions',
      'POST',
      new URLSearchParams({ select: 'id' }),
      record,
    ) as Array<{ id?: string }> | null;
    subscriptionId = Array.isArray(result) ? String(result[0]?.id || '') : '';
    if (!subscriptionId) throw new Error('The notification request was not saved.');
  } catch (error) {
    console.error('Could not save an advance-booking request.');
    return json({ detail: 'We could not save your notification request. Please try again.' }, 502, cors);
  }

  const requestUrl = new URL(request.url);
  const confirmUrl = requestUrl.origin + '/api/advance-booking/confirm/' + token;
  const safeMovie = escapeHtml(movieTitle);
  const safeUrl = escapeHtml(confirmUrl);
  const cinemaList = cinemaNames.map((name) => '<li style="padding:5px 0">' + escapeHtml(name) + '</li>').join('');
  const subject = 'Confirm your advance-booking alert for ' + movieTitle;
  const plainText =
    'You asked FilmUp to email you when advance booking opens for ' + movieTitle + '.\n\n' +
    'Selected cinemas: ' + cinemaNames.join(', ') + '\n\n' +
    'Confirm your request: ' + confirmUrl + '\n\n' +
    'This link expires in 48 hours. If you did not request this, ignore this email.';
  const htmlContent =
    '<!doctype html><html><body style="margin:0;background:#09090d;padding:28px 12px;font-family:Arial,sans-serif;color:#f5f5f5">' +
    '<div style="max-width:560px;margin:auto;background:#15151d;border:1px solid #ffffff18;border-radius:20px;overflow:hidden">' +
    '<div style="padding:28px 30px 12px"><div style="font-size:12px;letter-spacing:3px;color:#f5c26b;font-weight:bold">FILMUP · ADVANCE BOOKING</div>' +
    '<h1 style="font-size:28px;line-height:1.15;margin:18px 0 8px">One quick confirmation.</h1>' +
    '<p style="color:#c7c7d1;line-height:1.7">You asked us to email you when advance booking opens for <strong style="color:#fff">' + safeMovie + '</strong>. We will only send booking alerts after you confirm.</p></div>' +
    '<div style="margin:8px 30px 22px;padding:18px 20px;background:#ffffff08;border:1px solid #ffffff12;border-radius:14px">' +
    '<div style="font-size:11px;letter-spacing:1.5px;color:#f5c26b;font-weight:bold">YOUR SELECTED CINEMAS</div><ul style="margin:10px 0 0;padding-left:20px;color:#e7e7ec">' + cinemaList + '</ul></div>' +
    '<div style="padding:0 30px 30px"><a href="' + safeUrl + '" style="display:block;text-align:center;background:#f5c26b;color:#17120a;text-decoration:none;font-weight:bold;padding:15px 20px;border-radius:11px">Confirm my email alerts</a>' +
    '<p style="margin-top:18px;color:#92929e;font-size:12px;line-height:1.6">This link expires in 48 hours. If you did not request this, you can safely ignore this email.</p></div></div></body></html>';

  const sent = await sendResendEmail(env, email, subject, plainText, htmlContent);
  if (!sent) {
    try {
      await supabaseJson(
        env,
        'advance_booking_subscriptions',
        'DELETE',
        new URLSearchParams({ id: 'eq.' + subscriptionId }),
      );
    } catch {
      console.error('Could not remove the pending subscription after Resend delivery failed.');
    }
    return json({ detail: 'We could not send the confirmation email. Please check the email settings and try again.' }, 502, cors);
  }

  return json({ ok: true, message: 'Check your inbox and confirm your email to activate this alert.' }, 200, cors);
}

async function confirmAdvanceBooking(
  token: string,
  env: Env,
  origin: string | null,
  context: ExecutionContext,
): Promise<Response> {
  const cors = corsHeaders(origin, env);
  if (!env.SUPABASE_URL || !env.SUPABASE_KEY) {
    return confirmationPage('Service unavailable', 'FilmUp could not reach the notification database. Please try again later.');
  }

  let rows: Array<Record<string, unknown>>;
  try {
    rows = await supabaseJson(
      env,
      'advance_booking_subscriptions',
      'GET',
      new URLSearchParams({
        select: 'id,email,movie_title,cinema_names,expires_at,status',
        confirmation_token: 'eq.' + token,
        limit: '1',
      }),
    ) as Array<Record<string, unknown>>;
  } catch {
    return confirmationPage('Service unavailable', 'We could not verify this link right now. Please try again later.', false);
  }

  if (!rows.length) return confirmationPage('Link not found', 'This confirmation link is invalid or has already been removed.', false);
  const row = rows[0];
  const movieTitle = String(row.movie_title || 'your selected movie');
  if (row.status !== 'pending') {
    if (row.status === 'confirmed' || row.status === 'notified') {
      return confirmationPage("You're confirmed", 'Your advance-booking alerts for ' + movieTitle + ' are already active.');
    }
    return confirmationPage('Link no longer active', 'This confirmation request is no longer active.', false);
  }

  const expiresAt = Date.parse(String(row.expires_at || ''));
  if (!Number.isFinite(expiresAt)) {
    return confirmationPage('Invalid confirmation', 'The confirmation record is incomplete. Please subscribe again.', false);
  }
  if (expiresAt <= Date.now()) {
    try {
      await supabaseJson(
        env,
        'advance_booking_subscriptions',
        'PATCH',
        new URLSearchParams({ id: 'eq.' + String(row.id), status: 'eq.pending' }),
        { status: 'expired' },
      );
    } catch {
      // The link is expired regardless of whether the cleanup update succeeds.
    }
    return confirmationPage('Confirmation expired', 'This link expired after 48 hours. Please subscribe again from FilmUp.', false);
  }

  try {
    const updated = await supabaseJson(
      env,
      'advance_booking_subscriptions',
      'PATCH',
      new URLSearchParams({ id: 'eq.' + String(row.id), status: 'eq.pending' }),
      { status: 'confirmed', confirmed_at: new Date().toISOString() },
    ) as Array<Record<string, unknown>> | null;
    if (!Array.isArray(updated) || !updated.length) {
      return confirmationPage("You're confirmed", 'Your advance-booking request has already been activated.');
    }
  } catch {
    return confirmationPage('Confirmation failed', 'We could not activate this alert. Please try again.', false);
  }

  // Run an immediate check after confirmation; the regular five-minute cron remains a fallback.
  context.waitUntil(dispatchJob(env, 'advance_booking_check'));
  return confirmationPage(
    "You're confirmed",
    'FilmUp will email you when advance booking opens for ' + movieTitle + ' at your selected cinema(s).',
  );
}

function safeEqual(left: string, right: string): boolean {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return difference === 0;
}

async function exactCount(env: Env, status?: string): Promise<number> {
  const params = new URLSearchParams({ select: 'run_id', limit: '1' });
  if (status) params.set('status', 'eq.' + status);
  const response = await fetch(supabaseRestUrl(env, 'scraper_runs', params), {
    headers: {
      apikey: env.SUPABASE_KEY,
      Authorization: 'Bearer ' + env.SUPABASE_KEY,
      Prefer: 'count=exact',
      Range: '0-0',
    },
  });
  if (!response.ok) throw new Error('Could not count scraper history.');
  const range = response.headers.get('Content-Range') ?? '';
  const total = Number(range.split('/').at(-1));
  return Number.isFinite(total) ? total : 0;
}

async function monitoring(request: Request, env: Env, url: URL, origin: string | null): Promise<Response> {
  const expected = env.ADMIN_MONITORING_TOKEN ?? '';
  const authorization = request.headers.get('Authorization') ?? '';
  const supplied = authorization.startsWith('Bearer ') ? authorization.slice(7).trim() : '';
  const headers = corsHeaders(origin, env);

  if (!expected) return json({ detail: 'Scraper monitoring is not configured for public access.' }, 503, headers);
  if (!safeEqual(supplied, expected)) return json({ detail: 'Administrator authentication required.' }, 401, headers);

  const limit = Number(url.searchParams.get('limit') ?? '20');
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
    return json({ detail: 'limit must be between 1 and 100' }, 422, headers);
  }

  try {
    const latestParams = new URLSearchParams({
      select: '*',
      order: 'started_at.desc',
      limit: String(limit),
    });
    const successParams = new URLSearchParams({
      select: 'finished_at',
      status: 'eq.success',
      order: 'started_at.desc',
      limit: '1',
    });
    const [latestResponse, total, successful, warnings, failed, successResponse] = await Promise.all([
      fetch(supabaseRestUrl(env, 'scraper_runs', latestParams), {
        headers: { apikey: env.SUPABASE_KEY, Authorization: 'Bearer ' + env.SUPABASE_KEY },
      }),
      exactCount(env),
      exactCount(env, 'success'),
      exactCount(env, 'completed_with_warnings'),
      Promise.all([exactCount(env, 'failed'), exactCount(env, 'interrupted')]).then((counts) => counts[0] + counts[1]),
      fetch(supabaseRestUrl(env, 'scraper_runs', successParams), {
        headers: { apikey: env.SUPABASE_KEY, Authorization: 'Bearer ' + env.SUPABASE_KEY },
      }),
    ]);
    if (!latestResponse.ok || !successResponse.ok) throw new Error('Could not read scraper history.');
    const runs = await latestResponse.json() as Array<Record<string, unknown>>;
    const successes = await successResponse.json() as Array<{ finished_at?: string }>;
    return json({
      latest: runs[0] ?? null,
      runs,
      totals: {
        total_runs: total,
        successful_runs: successful,
        warning_runs: warnings,
        failed_runs: failed,
      },
      last_success_at: successes[0]?.finished_at ?? null,
      database: 'supabase',
    }, 200, headers);
  } catch (error) {
    console.error('Scraper monitoring query failed', error);
    return json({ detail: 'Scraper history could not be queried.' }, 502, headers);
  }
}

async function dispatchJob(env: Env, job: 'scrape' | 'advance_booking_check'): Promise<void> {
  const token = env.GITHUB_ACTIONS_TOKEN;
  if (!token) {
    console.error('GITHUB_ACTIONS_TOKEN is missing; ' + job + ' was not dispatched.');
    return;
  }
  const repository = env.GITHUB_REPOSITORY || 'damnalee/filmup';
  const branch = env.GITHUB_BRANCH || 'main';
  const endpoint = 'https://api.github.com/repos/' + repository + '/actions/workflows/cloudflare-jobs.yml/dispatches';
  const response = await fetch(endpoint, {
    method: 'POST',
    headers: {
      Accept: 'application/vnd.github+json',
      Authorization: 'Bearer ' + token,
      'X-GitHub-Api-Version': '2022-11-28',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ ref: branch, inputs: { job } }),
  });
  if (response.status !== 204) {
    console.error('GitHub dispatch failed for ' + job + ': HTTP ' + response.status + ' ' + (await response.text()).slice(0, 400));
    return;
  }
  console.log('Dispatched FilmUp job: ' + job);
}

export default {
  async fetch(request: Request, env: Env, context: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const origin = request.headers.get('Origin');
    const cors = corsHeaders(origin, env);

    if (origin && !allowedOrigins(env).has(origin.replace(/\/$/, ''))) {
      return json({ detail: 'Origin is not allowed.' }, 403);
    }
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });

    if (request.method === 'GET' && url.pathname === '/health') {
      return json({
        status: 'ok',
        runtime: 'cloudflare-workers',
        timezone: 'Asia/Karachi',
        scheduler: 'cloudflare-cron-dispatches-github-actions',
      }, 200, cors);
    }

    if (request.method === 'GET' && url.pathname === '/api/movies') {
      const params = new URLSearchParams({
        select: CATALOG_FIELDS,
        order: 'movie_title.asc',
        limit: '1000',
      });
      const search = url.searchParams.get('q')?.trim();
      if (search) params.set('movie_title', 'ilike.*' + search.replace(/[,*]/g, '') + '*');
      return proxySupabase(env, 'movie_catalog', params, origin);
    }

    if (request.method === 'GET' && url.pathname === '/api/showtimes') {
      const params = new URLSearchParams({
        select: SHOWTIME_FIELDS,
        showtime: 'gte.' + new Date().toISOString(),
        order: 'showtime.asc',
        limit: '2000',
      });
      return proxySupabase(env, 'showtimes', params, origin);
    }

    const movieMatch = url.pathname.match(/^\/api\/movies\/([^/]+)$/);
    if (request.method === 'GET' && movieMatch) {
      const params = new URLSearchParams({
        select: CATALOG_FIELDS,
        id: 'eq.' + decodeURIComponent(movieMatch[1]),
        limit: '1',
      });
      return proxySupabase(env, 'movie_catalog', params, origin);
    }

    if (request.method === 'GET' && url.pathname === '/api/monitoring/scraper') {
      return monitoring(request, env, url, origin);
    }
    if (request.method === 'POST' && url.pathname === '/api/advance-booking/subscribe') {
      return subscribeForAdvanceBooking(request, env, origin);
    }
    const confirmationMatch = url.pathname.match(/^\/api\/advance-booking\/confirm\/([A-Za-z0-9_-]+)$/);
    if (request.method === 'GET' && confirmationMatch) {
      return confirmAdvanceBooking(confirmationMatch[1], env, origin, context);
    }
    if (url.pathname === '/chat') {
      return json({ detail: 'The optional chat service is not configured on the Cloudflare edge API yet.' }, 503, cors);
    }
    return json({ detail: 'Not Found' }, 404, cors);
  },

  async scheduled(controller: ScheduledController, env: Env, context: ExecutionContext): Promise<void> {
    if (controller.cron === '5 * * * *') {
      context.waitUntil(dispatchJob(env, 'scrape'));
    } else if (controller.cron === '*/5 * * * *') {
      context.waitUntil(dispatchJob(env, 'advance_booking_check'));
    } else {
      console.warn('Unrecognized FilmUp cron expression: ' + controller.cron);
    }
  },
};
