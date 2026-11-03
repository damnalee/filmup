import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

load_dotenv(BASE_DIR / ".env")

TIMEZONE_NAME = os.getenv("TIMEZONE", "Asia/Karachi").strip()
APP_TIMEZONE = ZoneInfo(TIMEZONE_NAME)

SCRAPER_ENABLED = (
    os.getenv("SCRAPER_ENABLED", "true").strip().lower()
    in {"1", "true", "yes", "on"}
)
SCRAPER_SCHEDULE_MINUTE = int(
    os.getenv("SCRAPER_SCHEDULE_MINUTE", "5")
)
SCRAPER_INTERVAL_HOURS = int(
    os.getenv("SCRAPER_INTERVAL_HOURS", "1")
)

if not 0 <= SCRAPER_SCHEDULE_MINUTE <= 59:
    raise ValueError("SCRAPER_SCHEDULE_MINUTE must be between 0 and 59.")

if not 1 <= SCRAPER_INTERVAL_HOURS <= 24:
    raise ValueError("SCRAPER_INTERVAL_HOURS must be between 1 and 24.")

logging.basicConfig(
    level=getattr(
        logging,
        os.getenv("LOG_LEVEL", "INFO").upper(),
        logging.INFO,
    ),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

from scraper import run_scrape_job
from scraper_monitoring import get_monitoring_snapshot, run_monitored_scrape
from chatbot import chat_with_cinema, GROQ_MODEL
from advance_booking_routes import (
    router as advance_booking_router,
    check_advance_booking_notifications,
)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=10)


scheduler = None


async def monitored_scrape_job():
    """Keep APScheduler's coroutine handling while observing the original job."""
    await run_monitored_scrape(run_scrape_job, trigger="scheduled")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler

    if SCRAPER_ENABLED:
        scheduler = AsyncIOScheduler(timezone=APP_TIMEZONE)

        scheduler.add_job(
            monitored_scrape_job,
            trigger=CronTrigger(
                minute=SCRAPER_SCHEDULE_MINUTE,
                hour=f"*/{SCRAPER_INTERVAL_HOURS}",
                timezone=APP_TIMEZONE,
            ),
            id="hourly_lahore_cinema_scrape",
            name="Lahore cinema showtime refresh",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=600,
        )

        scheduler.add_job(
            check_advance_booking_notifications,
            trigger=IntervalTrigger(minutes=5),
            id="advance_booking_notification_check",
            name="Check confirmed advance-booking alerts",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )

        scheduler.start()
        job = scheduler.get_job("hourly_lahore_cinema_scrape")

        logger.info("Cinema scraper scheduler started.")
        logger.info("Timezone: %s", TIMEZONE_NAME)
        logger.info(
            "Next scheduled scrape: %s",
            job.next_run_time.isoformat() if job else "unknown",
        )
    else:
        logger.info("Cinema scraper scheduler is disabled.")

    try:
        yield
    finally:
        if scheduler is not None and scheduler.running:
            scheduler.shutdown(wait=False)
            logger.info("Cinema scraper scheduler stopped.")


from catalog_routes import router as catalog_router
from database import SUPABASE_CONFIGURED

app = FastAPI(
    title="FilmUp API",
    version="1.0.0",
    description="FilmUp API for Lahore cinema listings, showtimes and advance-booking notifications.",
    lifespan=lifespan,
)

# The Vite development proxy avoids CORS locally. Production frontend origins
# must be explicitly allow-listed through CORS_ALLOWED_ORIGINS.
CORS_ALLOWED_ORIGINS = [
    origin.strip().rstrip("/")
    for origin in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Accept", "Content-Type", "Authorization"],
)
app.include_router(catalog_router)
app.include_router(advance_booking_router)

# Serve CSS, JavaScript and other frontend assets.
app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR)),
    name="static",
)


@app.get("/")
async def root():
    index_file = STATIC_DIR / "index.html"

    if not index_file.is_file():
        raise HTTPException(
            status_code=500,
            detail="Frontend file static/index.html was not found.",
        )

    return FileResponse(index_file)


@app.get("/health")
@app.get("/api/health")
async def health():
    job = (
        scheduler.get_job("hourly_lahore_cinema_scrape")
        if scheduler is not None and scheduler.running
        else None
    )

    return {
        "status": "ok" if SUPABASE_CONFIGURED else "degraded",
        "database_configured": SUPABASE_CONFIGURED,
        "current_time": datetime.now(APP_TIMEZONE).isoformat(),
        "timezone": TIMEZONE_NAME,
        "scraper_enabled": SCRAPER_ENABLED,
        "scheduler_running": bool(
            scheduler is not None and scheduler.running
        ),
        "next_scrape_at": (
            job.next_run_time.isoformat()
            if job and job.next_run_time
            else None
        ),
    }


@app.get("/api/monitoring/scraper")
async def scraper_monitoring(limit: int = 20):
    """Read-only operational view of scraper executions and audit summaries."""
    if not 1 <= limit <= 100:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 100")
    return get_monitoring_snapshot(limit=limit)


@app.post("/api/chat")
async def chat(request: ChatRequest):
    try:
        answer = await chat_with_cinema(
            user_message=request.message,
            history=[
                entry.model_dump()
                for entry in request.history
            ],
        )

        return {
            "answer": answer,
            "model": GROQ_MODEL,
        }

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception("Chat request failed.")
        raise HTTPException(
            status_code=502,
            detail="The chat service is temporarily unavailable.",
        ) from exc
