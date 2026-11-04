"""Email-confirmed advance-booking subscriptions for FilmUp."""

import html
import logging
import os
import re
import secrets
import unicodedata
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from database import SUPABASE_CONFIGURED, supabase
from email_service import resend_config, send_resend_email

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/advance-booking", tags=["advance-booking"])
PK_TZ = ZoneInfo("Asia/Karachi")
CONFIRMATION_HOURS = 48


class AdvanceBookingSubscriptionRequest(BaseModel):
    movie_title: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=5, max_length=320)
    cinema_names: list[str] = Field(min_length=1, max_length=20)


def _email_config():
    return resend_config()


def _send_email(to_email: str, subject: str, plain_text: str, html_body: str):
    return send_resend_email(to_email, subject, plain_text, html_body)



def _clean_email(value: str) -> str:
    email = value.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    return email


def _title_key(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()
    value = re.sub(r"\([^)]*\)", " ", value)
    value = re.sub(r"\b(?:18\s*\+|16\s*\+|13\s*\+|2d|3d|4dx|imax|urdu|english|hindi|punjabi|dubbed|subtitled)\b", " ", value)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())


def _cinema_key(value: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))


def _slot_key(row: dict) -> str | None:
    """Return a stable UTC-microsecond key for one cinema showtime."""
    raw = str(row.get("showtime") or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        delta = parsed.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
        return str(
            delta.days * 86_400_000_000
            + delta.seconds * 1_000_000
            + delta.microseconds
        )
    except (ValueError, TypeError, OverflowError):
        return None


def _new_slots_by_cinema(
    rows: list[dict],
    selected_cinemas: list[str],
    notified_slots: dict,
) -> dict[str, list[dict]]:
    """Keep only selected-cinema showtimes not previously included in an alert."""
    selected_keys = {_cinema_key(name): name for name in selected_cinemas}
    newly_available: dict[str, list[dict]] = {}
    for row in rows:
        cinema_key = _cinema_key(str(row.get("cinema_name") or "").strip())
        selected_name = selected_keys.get(cinema_key)
        slot_key = _slot_key(row)
        if not selected_name or slot_key is None:
            continue
        seen = set(notified_slots.get(cinema_key) or [])
        if slot_key in seen:
            continue
        newly_available.setdefault(selected_name, []).append(row)
    return newly_available


def _page(title: str, message: str, success: bool = True) -> HTMLResponse:
    safe_title = html.escape(title)
    safe_message = html.escape(message)
    color = "#f5c26b" if success else "#ff8b8b"
    return HTMLResponse(f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_title} · FilmUp</title>
<body style="margin:0;min-height:100vh;display:grid;place-items:center;background:#09090d;color:#f5f5f5;font:16px/1.6 Arial,sans-serif">
<main style="max-width:520px;margin:24px;padding:36px;border:1px solid #ffffff18;border-radius:22px;background:#14141b;text-align:center">
<div style="font-size:13px;letter-spacing:.2em;color:{color};font-weight:700">FILMUP</div>
<h1 style="font-size:30px;margin:14px 0 8px">{safe_title}</h1>
<p style="color:#c7c7d1">{safe_message}</p>
<p style="margin-top:28px;color:#858592;font-size:13px">You can close this page and return to FilmUp.</p>
</main></body></html>""")


def _format_slot(row: dict) -> tuple[str, str]:
    raw = str(row.get("showtime") or "")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        local = parsed.astimezone(PK_TZ)
        return local.strftime("%a, %d %b %Y · %I:%M %p"), local.isoformat()
    except (ValueError, TypeError):
        return raw, raw


def _confirmation_email(movie_title: str, cinema_names: list[str], confirm_url: str) -> tuple[str, str, str]:
    safe_movie = html.escape(movie_title)
    cinema_list = "".join(f"<li style='padding:5px 0'>{html.escape(name)}</li>" for name in cinema_names)
    safe_url = html.escape(confirm_url, quote=True)
    subject = f"Confirm your advance-booking alert for {movie_title}"
    plain = (
        f"You asked FilmUp to email you when advance booking opens for {movie_title}.\n\n"
        f"Selected cinemas: {', '.join(cinema_names)}\n\n"
        f"Confirm your request: {confirm_url}\n\n"
        f"This link expires in {CONFIRMATION_HOURS} hours. If you did not request this, ignore this email."
    )
    body = f"""<!doctype html><html><body style="margin:0;background:#09090d;padding:28px 12px;font-family:Arial,sans-serif;color:#f5f5f5">
<div style="max-width:560px;margin:auto;background:#15151d;border:1px solid #ffffff18;border-radius:20px;overflow:hidden">
<div style="padding:28px 30px 12px"><div style="font-size:12px;letter-spacing:3px;color:#f5c26b;font-weight:bold">FILMUP · ADVANCE BOOKING</div>
<h1 style="font-size:28px;line-height:1.15;margin:18px 0 8px">One quick confirmation.</h1>
<p style="color:#c7c7d1;line-height:1.7">You asked us to email you when advance booking opens for <strong style="color:#fff">{safe_movie}</strong>. We will only send booking alerts after you confirm.</p>
</div>
<div style="margin:8px 30px 22px;padding:18px 20px;background:#ffffff08;border:1px solid #ffffff12;border-radius:14px">
<div style="font-size:11px;letter-spacing:1.5px;color:#f5c26b;font-weight:bold">YOUR SELECTED CINEMAS</div><ul style="margin:10px 0 0;padding-left:20px;color:#e7e7ec">{cinema_list}</ul>
</div>
<div style="padding:0 30px 30px"><a href="{safe_url}" style="display:block;text-align:center;background:#f5c26b;color:#17120a;text-decoration:none;font-weight:bold;padding:15px 20px;border-radius:11px">Confirm my email alerts</a>
<p style="font-size:12px;color:#92929e;line-height:1.6;margin-top:18px">This confirmation link expires in 48 hours. If you did not make this request, you can safely ignore this email. No alerts will be sent unless you confirm.</p></div>
</div></body></html>"""
    return subject, plain, body


def _booking_email(movie_title: str, cinema_matches: dict[str, list[dict]]) -> tuple[str, str, str]:
    safe_movie = html.escape(movie_title)
    sections_html = []
    sections_text = []
    for cinema_name, rows in cinema_matches.items():
        safe_cinema = html.escape(cinema_name)
        items_html = []
        items_text = []
        for row in rows:
            display, _ = _format_slot(row)
            safe_display = html.escape(display)
            booking_link = str(row.get("booking_link") or "").strip()
            if booking_link.startswith(("https://", "http://")):
                safe_link = html.escape(booking_link, quote=True)
                items_html.append(f"<li style='padding:8px 0'>{safe_display} · <a href='{safe_link}' style='color:#f5c26b'>Book tickets</a></li>")
                items_text.append(f"- {display}: {booking_link}")
            else:
                items_html.append(f"<li style='padding:8px 0'>{safe_display}</li>")
                items_text.append(f"- {display}")
        sections_html.append(
            f"<div style='margin:14px 0;padding:18px 20px;background:#ffffff08;border:1px solid #ffffff12;border-radius:14px'>"
            f"<h2 style='font-size:17px;margin:0 0 8px;color:#f5c26b'>{safe_cinema}</h2><ul style='padding-left:20px;margin:0;color:#e7e7ec'>{''.join(items_html)}</ul></div>"
        )
        sections_text.append(f"{cinema_name}\n" + "\n".join(items_text))
    subject = f"Advance booking is open: {movie_title}"
    plain = (
        f"Advance booking has been detected for {movie_title} at your selected cinema(s).\n\n"
        + "\n\n".join(sections_text)
        + "\n\nFilmUp sent this because you confirmed an advance-booking alert."
    )
    body = f"""<!doctype html><html><body style="margin:0;background:#09090d;padding:28px 12px;font-family:Arial,sans-serif;color:#f5f5f5">
<div style="max-width:560px;margin:auto;background:#15151d;border:1px solid #ffffff18;border-radius:20px;padding:28px 30px">
<div style="font-size:12px;letter-spacing:3px;color:#f5c26b;font-weight:bold">FILMUP · BOOKING ALERT</div>
<h1 style="font-size:29px;line-height:1.15;margin:18px 0 8px">Advance booking is open.</h1>
<p style="color:#c7c7d1;line-height:1.7">Good news — booking slots for <strong style="color:#fff">{safe_movie}</strong> have appeared at a cinema you selected.</p>
{''.join(sections_html)}
<p style="font-size:12px;color:#92929e;line-height:1.6;margin-top:22px">This is an availability alert, not a ticket reservation. Seats may sell out or availability may change. Check the cinema's booking page before travelling.</p>
</div></body></html>"""
    return subject, plain, body


@router.post("/subscribe")
def subscribe_to_advance_booking(payload: AdvanceBookingSubscriptionRequest, request: Request):
    """Create a pending subscription and email a double-opt-in confirmation link."""
    email = _clean_email(payload.email)
    movie_title = " ".join(payload.movie_title.split())
    cinema_names = list(dict.fromkeys(" ".join(name.split()) for name in payload.cinema_names if name.strip()))
    if not cinema_names:
        raise HTTPException(status_code=422, detail="Select at least one cinema.")
    if any(len(name) > 120 for name in cinema_names):
        raise HTTPException(status_code=422, detail="A cinema name is too long.")

    try:
        _email_config()
    except Exception as exc:
        logger.error("Advance-booking email is not configured: %s", exc)
        raise HTTPException(status_code=503, detail="Email notifications are not configured yet.") from exc

    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=CONFIRMATION_HOURS)).isoformat()
    record = {
        "email": email,
        "movie_title": movie_title,
        "cinema_names": cinema_names,
        "notified_cinemas": [],
        "notified_slots": {},
        "confirmation_token": token,
        "status": "pending",
        "expires_at": expires_at,
    }
    try:
        inserted = supabase.table("advance_booking_subscriptions").insert(record).execute()
        if not inserted.data:
            raise RuntimeError("Supabase did not return the inserted subscription.")
        subscription_id = inserted.data[0]["id"]
    except Exception as exc:
        logger.exception("Could not save advance-booking subscription.")
        raise HTTPException(status_code=502, detail="We couldn't save your notification request. Please try again.") from exc

    confirm_url = str(request.url_for("confirm_advance_booking", token=token))
    subject, plain, body = _confirmation_email(movie_title, cinema_names, confirm_url)
    try:
        _send_email(email, subject, plain, body)
    except Exception as exc:
        logger.exception("Could not send advance-booking confirmation email.")
        try:
            supabase.table("advance_booking_subscriptions").delete().eq("id", subscription_id).execute()
        except Exception:
            logger.exception("Could not clean up subscription after confirmation email failure.")
        raise HTTPException(status_code=502, detail="We couldn't send the confirmation email. Check the email settings and try again.") from exc

    return {"ok": True, "message": "Check your inbox and confirm your email to activate this alert."}


@router.get("/confirm/{token}", name="confirm_advance_booking", response_class=HTMLResponse)
def confirm_advance_booking(token: str):
    """Activate a pending subscription when its recipient follows the email link."""
    try:
        result = (
            supabase.table("advance_booking_subscriptions")
            .select("id,email,movie_title,cinema_names,expires_at,status")
            .eq("confirmation_token", token)
            .limit(1)
            .execute()
        )
        rows = result.data or []
    except Exception as exc:
        logger.exception("Could not look up advance-booking confirmation.")
        raise HTTPException(status_code=502, detail="We couldn't verify this link right now.") from exc

    if not rows:
        return _page("Link not found", "This confirmation link is invalid or has already been removed.", False)
    row = rows[0]
    if row.get("status") != "pending":
        if row.get("status") in {"confirmed", "notified"}:
            return _page("You're confirmed", f"Your advance-booking alerts for {row.get('movie_title')} are already active.")
        return _page("Link no longer active", "This confirmation request is no longer active.", False)

    try:
        expires = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
        if expires <= datetime.now(timezone.utc):
            supabase.table("advance_booking_subscriptions").update({"status": "expired"}).eq("id", row["id"]).execute()
            return _page("Confirmation expired", "This link expired after 48 hours. Please subscribe again from FilmUp.", False)
    except (KeyError, ValueError, TypeError):
        return _page("Invalid confirmation", "The confirmation record is incomplete. Please subscribe again.", False)

    try:
        supabase.table("advance_booking_subscriptions").update({
            "status": "confirmed",
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", row["id"]).execute()
    except Exception as exc:
        logger.exception("Could not activate advance-booking subscription.")
        raise HTTPException(status_code=502, detail="We couldn't activate this alert. Please try again.") from exc

    # If booking is already live at the moment of confirmation, alert immediately.
    try:
        check_advance_booking_notifications(subscription_id=row["id"])
    except Exception:
        logger.exception("Immediate post-confirmation booking check failed.")

    return _page(
        "You're confirmed",
        f"FilmUp will email {row.get('email')} when advance booking opens for {row.get('movie_title')} at your selected cinema(s).",
    )


def check_advance_booking_notifications(
    subscription_id: str | None = None,
    *,
    dry_run: bool | None = None,
) -> int:
    """Detect newly appearing selected-cinema showtimes and email only unseen slots.

    Live email delivery is opt-in through ADVANCE_BOOKING_EMAIL_ENABLED=true.
    Dry runs never send mail or mark slots notified. A subscription remains eligible
    after its first alert so later showtimes at the same cinema can trigger follow-ups.
    """
    if dry_run is None:
        dry_run = os.getenv("ADVANCE_BOOKING_EMAIL_ENABLED", "false").strip().lower() not in {
            "1", "true", "yes", "on"
        }
    if not SUPABASE_CONFIGURED or supabase is None:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_KEY must be configured before advance-booking jobs run."
        )

    query = (
        supabase.table("advance_booking_subscriptions")
        .select("id,email,movie_title,cinema_names,notified_cinemas,notified_slots")
        .in_("status", ["confirmed", "notified"])
    )
    if subscription_id:
        query = query.eq("id", subscription_id)
    subscriptions = query.limit(500).execute().data or []
    if not dry_run:
        # Fail before processing subscriptions if live delivery is misconfigured.
        _email_config()
    now = datetime.now(timezone.utc)
    checked_count = len(subscriptions)
    sent_count = 0
    detected_count = 0
    no_match_count = 0
    failed_count = 0

    for subscription in subscriptions:
        movie_title = str(subscription.get("movie_title") or "").strip()
        selected = subscription.get("cinema_names") or []
        already_notified = set(subscription.get("notified_cinemas") or [])
        already_notified_slots = subscription.get("notified_slots") or {}
        if not movie_title or not selected:
            continue

        try:
            rows = (
                supabase.table("showtimes")
                .select("movie_title,cinema_name,showtime,booking_link,ticket_price")
                .ilike("movie_title", f"%{movie_title}%")
                .gte("showtime", now.isoformat())
                .order("showtime")
                .limit(2000)
                .execute()
                .data or []
            )
        except Exception:
            failed_count += 1
            logger.exception("Could not check showtimes for one advance-booking subscription.")
            continue

        matching_movie_rows = [
            row for row in rows
            if _title_key(str(row.get("movie_title") or "")) == _title_key(movie_title)
        ]
        newly_available = _new_slots_by_cinema(
            matching_movie_rows,
            selected,
            already_notified_slots,
        )

        if not newly_available:
            no_match_count += 1
            continue

        detected_count += 1
        if dry_run:
            logger.info("DRY RUN: new matching booking slots detected for one subscription.")
            continue

        subject, plain, body = _booking_email(movie_title, newly_available)
        try:
            _send_email(str(subscription["email"]), subject, plain, body)
            sent_count += 1
        except Exception:
            failed_count += 1
            logger.exception("Booking alert email failed for one advance-booking subscription.")
            continue

        updated_notified = sorted(already_notified.union(newly_available.keys()))
        updated_slots = {
            str(cinema_key): list(dict.fromkeys(str(slot) for slot in slots))
            for cinema_key, slots in already_notified_slots.items()
        }
        for cinema_name, new_rows in newly_available.items():
            cinema_key = _cinema_key(cinema_name)
            known = set(updated_slots.get(cinema_key) or [])
            known.update(slot for slot in (_slot_key(row) for row in new_rows) if slot)
            updated_slots[cinema_key] = sorted(known)
        all_notified = set(updated_notified) >= set(selected)
        try:
            supabase.table("advance_booking_subscriptions").update({
                "notified_cinemas": updated_notified,
                "notified_slots": updated_slots,
                "status": "notified" if all_notified else "confirmed",
                "last_notified_at": now.isoformat(),
            }).eq("id", subscription["id"]).execute()
            logger.info("Resend accepted one advance-booking alert; subscription state updated.")
        except Exception:
            # Email was sent; log loudly so a failed state update can be reconciled.
            failed_count += 1
            logger.exception("Alert was sent but subscription state update failed.")

    logger.info(
        "Advance-booking check summary: mode=%s confirmed_subscriptions=%d "
        "subscriptions_without_new_slots=%d subscriptions_with_new_slots=%d "
        "alerts_sent=%d failures=%d.",
        "dry-run" if dry_run else "live",
        checked_count,
        no_match_count,
        detected_count,
        sent_count,
        failed_count,
    )
    return detected_count if dry_run else sent_count
