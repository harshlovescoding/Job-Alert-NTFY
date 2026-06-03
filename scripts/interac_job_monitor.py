#!/usr/bin/env python3
"""Check Interac's Workday jobs API for newly posted roles."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


API_URL = "https://interac.wd3.myworkdayjobs.com/wday/cxs/interac/Interac/jobs"
JOB_BASE_URL = "https://interac.wd3.myworkdayjobs.com/en-US/Interac"
DEFAULT_STATE_PATH = Path("data/seen_jobs.json")
DEFAULT_NTFY_TOPIC_PATH = Path("data/ntfy_topic.txt")
WORKDAY_PAGE_LIMIT = 20
MAX_SMS_LENGTH = 1500
MAX_NTFY_LENGTH = 3500


@dataclass(frozen=True)
class Job:
    requisition_id: str
    title: str
    location: str
    posted_on: str
    url: str


def fetch_jobs(limit: int = 100) -> list[Job]:
    jobs: list[Job] = []
    offset = 0
    while len(jobs) < limit:
        page = fetch_job_page(limit=min(WORKDAY_PAGE_LIMIT, limit - len(jobs)), offset=offset)
        jobs.extend(page["jobs"])
        if len(jobs) >= page["total"] or not page["jobs"]:
            break
        offset += len(page["jobs"])
    return jobs


def fetch_job_page(limit: int, offset: int) -> dict[str, Any]:
    payload = json.dumps(
        {
            "appliedFacets": {},
            "limit": limit,
            "offset": offset,
            "searchText": "",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "interac-job-monitor/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach Interac Workday API: {exc}") from exc

    data = json.loads(body)
    jobs: list[Job] = []
    for posting in data.get("jobPostings", []):
        requisition_id = ""
        bullet_fields = posting.get("bulletFields") or []
        if bullet_fields:
            requisition_id = str(bullet_fields[0])
        if not requisition_id:
            requisition_id = str(posting.get("externalPath", ""))

        external_path = str(posting.get("externalPath", ""))
        url = f"{JOB_BASE_URL}{external_path}" if external_path.startswith("/") else external_path
        jobs.append(
            Job(
                requisition_id=requisition_id,
                title=str(posting.get("title", "Untitled role")),
                location=str(posting.get("locationsText", "Unknown location")),
                posted_on=str(posting.get("postedOn", "Unknown posted date")),
                url=url,
            )
        )
    return {"jobs": jobs, "total": int(data.get("total", len(jobs)))}


def load_seen(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"seen": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_seen(path: Path, jobs: list[Job], prior_state: dict[str, Any]) -> None:
    seen = dict(prior_state.get("seen", {}))
    for job in jobs:
        seen[job.requisition_id] = {
            "title": job.title,
            "location": job.location,
            "posted_on": job.posted_on,
            "url": job.url,
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"seen": seen}, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def format_jobs(jobs: list[Job]) -> str:
    lines = []
    for job in jobs:
        lines.append(f"- {job.title} ({job.requisition_id})")
        lines.append(f"  Location: {job.location}")
        lines.append(f"  Posted: {job.posted_on}")
        lines.append(f"  Apply: {job.url}")
    return "\n".join(lines)


def build_sms_body(jobs: list[Job]) -> str:
    lines = [f"New Interac job posting{'s' if len(jobs) != 1 else ''}:"]
    for job in jobs:
        lines.append(f"{job.title} ({job.requisition_id})")
        lines.append(f"{job.location} | {job.posted_on}")
        lines.append(job.url)
    body = "\n".join(lines)
    if len(body) <= MAX_SMS_LENGTH:
        return body
    return body[: MAX_SMS_LENGTH - 40].rstrip() + "\n...more jobs found; check Codex."


def send_twilio_sms(body: str) -> None:
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_FROM_NUMBER")
    to_number = os.environ.get("SMS_TO_NUMBER")
    missing = [
        name
        for name, value in {
            "TWILIO_ACCOUNT_SID": account_sid,
            "TWILIO_AUTH_TOKEN": auth_token,
            "TWILIO_FROM_NUMBER": from_number,
            "SMS_TO_NUMBER": to_number,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"SMS requested but missing env vars: {', '.join(missing)}")

    payload = urllib.parse.urlencode(
        {
            "From": from_number,
            "To": to_number,
            "Body": body,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
        data=payload,
        method="POST",
    )
    credentials = f"{account_sid}:{auth_token}".encode("utf-8")
    request.add_header("Authorization", "Basic " + __import__("base64").b64encode(credentials).decode("ascii"))
    request.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status >= 300:
                raise RuntimeError(f"Twilio returned HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Twilio SMS failed: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Twilio SMS failed: {exc}") from exc


def load_ntfy_topic(path: Path) -> str:
    env_topic = os.environ.get("NTFY_TOPIC", "").strip()
    if env_topic:
        return env_topic
    if not path.exists():
        raise RuntimeError(f"ntfy requested but topic file does not exist: {path}")
    topic = path.read_text(encoding="utf-8").strip()
    if not topic:
        raise RuntimeError(f"ntfy requested but topic file is empty: {path}")
    return topic


def build_ntfy_body(jobs: list[Job]) -> str:
    body = format_jobs(jobs)
    if len(body) <= MAX_NTFY_LENGTH:
        return body
    return body[: MAX_NTFY_LENGTH - 45].rstrip() + "\n...more jobs found; check Codex."


def send_ntfy_notification(topic: str, jobs: list[Job]) -> None:
    body = build_ntfy_body(jobs).encode("utf-8")
    request = urllib.request.Request(
        f"https://ntfy.sh/{urllib.parse.quote(topic)}",
        data=body,
        headers={
            "Title": f"New Interac job{'s' if len(jobs) != 1 else ''}",
            "Tags": "briefcase",
            "Priority": "high",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status >= 300:
                raise RuntimeError(f"ntfy returned HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ntfy notification failed: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"ntfy notification failed: {exc}") from exc


def send_ntfy_test(topic: str) -> None:
    request = urllib.request.Request(
        f"https://ntfy.sh/{urllib.parse.quote(topic)}",
        data=b"Interac job monitor test notification. If you see this, phone alerts are working.",
        headers={
            "Title": "Interac monitor test",
            "Tags": "white_check_mark",
            "Priority": "default",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status >= 300:
                raise RuntimeError(f"ntfy returned HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ntfy test failed: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"ntfy test failed: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor Interac Workday jobs for new postings.")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH, help="Path to seen-jobs state JSON.")
    parser.add_argument("--init-current", action="store_true", help="Mark current jobs as seen without alerting.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum jobs to fetch.")
    parser.add_argument("--sms", action="store_true", help="Send an SMS with new jobs via Twilio.")
    parser.add_argument("--ntfy", action="store_true", help="Send a phone push notification via ntfy.")
    parser.add_argument("--test-ntfy", action="store_true", help="Send a test ntfy notification and exit.")
    parser.add_argument(
        "--ntfy-topic-file",
        type=Path,
        default=DEFAULT_NTFY_TOPIC_PATH,
        help="Path to the ntfy topic file.",
    )
    args = parser.parse_args()

    if args.test_ntfy:
        send_ntfy_test(load_ntfy_topic(args.ntfy_topic_file))
        print("ntfy test notification sent.")
        return 0

    jobs = fetch_jobs(limit=args.limit)
    state = load_seen(args.state)
    seen_ids = set(state.get("seen", {}).keys())
    new_jobs = [job for job in jobs if job.requisition_id not in seen_ids]

    if args.init_current:
        save_seen(args.state, jobs, state)
        print(f"Baseline saved: {len(jobs)} current Interac jobs marked as seen.")
        return 0

    if not new_jobs:
        save_seen(args.state, jobs, state)
        print(f"No new Interac jobs. Checked {len(jobs)} active postings.")
        return 0

    print(f"New Interac job posting{'s' if len(new_jobs) != 1 else ''} found:")
    print(format_jobs(new_jobs))
    if args.sms:
        send_twilio_sms(build_sms_body(new_jobs))
        print("SMS notification sent.")
    if args.ntfy:
        send_ntfy_notification(load_ntfy_topic(args.ntfy_topic_file), new_jobs)
        print("ntfy notification sent.")
    save_seen(args.state, jobs, state)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Interac job monitor failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
