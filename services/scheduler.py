"""Background scheduler for automatic domain scanning.

Uses APScheduler to run scan jobs based on per-domain schedule config.
Schedule config is stored in domains.scan_schedule (JSONB).
"""
import json
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from services import supabase_client as db

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(daemon=True)


def init_scheduler():
    """Load all schedules from DB and start the scheduler. Call once at app startup."""
    load_all_schedules()
    scheduler.start()
    logger.info("Scheduler started")


def _parse_schedule(raw):
    """Parse scan_schedule which may be a JSON string or dict."""
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    return raw


def load_all_schedules():
    """Read all domains with scan_schedule and create/update jobs."""
    try:
        domains = db.select("domains", {
            "select": "id,url,scan_schedule",
        })
        scheduled = []
        for d in domains:
            sched = _parse_schedule(d.get("scan_schedule"))
            if sched:
                d["scan_schedule"] = sched
                scheduled.append(d)
        for domain in scheduled:
            add_or_update_job(domain)
        logger.info(f"Loaded {len(scheduled)} scheduled scan jobs")
    except Exception as e:
        logger.warning(f"Failed to load schedules (column may not exist yet): {e}")


def add_or_update_job(domain):
    """Create or replace a scheduled job for a domain."""
    job_id = f"scan_{domain['id']}"
    schedule = domain.get("scan_schedule")

    # Remove existing job
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass

    if not schedule or not schedule.get("mode"):
        return

    mode = schedule["mode"]

    if mode == "daily":
        time_str = schedule.get("time", "02:00")
        hour, minute = time_str.split(":")
        trigger = CronTrigger(hour=int(hour), minute=int(minute))
    elif mode == "interval":
        hours = max(1, int(schedule.get("hours", 6)))
        trigger = IntervalTrigger(hours=hours)
    else:
        return

    scheduler.add_job(
        _run_scheduled_scan,
        trigger=trigger,
        args=[domain["id"], schedule],
        id=job_id,
        replace_existing=True,
        misfire_grace_time=3600,  # 1 hour grace for missed jobs
    )
    logger.info(f"Scheduled job {job_id}: mode={mode}")


def remove_job(domain_id):
    """Remove a scheduled job for a domain."""
    job_id = f"scan_{domain_id}"
    try:
        scheduler.remove_job(job_id)
        logger.info(f"Removed job {job_id}")
    except Exception:
        pass


def _run_scheduled_scan(domain_id, schedule):
    """Execute a scheduled scan using shared scan executor."""
    from services.scan_executor import execute_scan

    crawl_method = schedule.get("crawl_method", "auto")
    max_pages = int(schedule.get("max_pages", 200))
    max_depth = int(schedule.get("max_depth", 2))

    logger.info(f"Running scheduled scan for domain {domain_id}")
    try:
        result = execute_scan(
            domain_id,
            crawl_method=crawl_method,
            max_depth=max_depth,
            max_pages=max_pages,
        )
        logger.info(f"Scheduled scan complete: {result['total_images']} images, {result['flagged_count']} flagged")
    except Exception as e:
        logger.error(f"Scheduled scan failed for {domain_id}: {e}")


def get_scheduled_jobs_info():
    """Return list of active scheduled jobs (for debugging/status)."""
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
            "trigger": str(job.trigger),
        })
    return jobs
