"""Shut a forgotten game-night host down from the host itself.

Nothing here calls AWS until a warning or a shutdown is actually due, and
the decision of *whether* to do that is pure: times in, action out. Tests
cover that function with no network and no boto.

Two clocks run together:

* Idle. No HTTP request has reached Caddy (ignoring Let's Encrypt) or the
  app since this boot. Opening the site resets it. Default warning at 60
  minutes, shutdown at 90.
* Uptime. How long this boot has lasted, plus any snooze. Default warning
  at 7 hours, shutdown at 8, even if people are still scoring.

The timer fires about every 15 minutes, so a limit can be that late.

Snooze is ``sudo /usr/local/bin/dutch-blitz-idle snooze``. It adds a fixed
number of minutes to the uptime deadline. It is root-only. There is no URL
that snoozes or powers the machine off. The app can update its activity
stamp, which only resets the idle clock. The uptime extra lives in a
root-owned state file.

Shutdown order, and it is this order on purpose: upload a snapshot with
the existing backup command, and only if that exits 0 send the final
email and run ``shutdown -h now``. EC2 deletes the instance because the
game-night stack sets ``instance_initiated_shutdown_behavior`` to
``terminate``. A failed backup leaves the host up and emails once.

``down`` on a laptop still has to delete the blitz DNS record. State and
that record stay until then. The shutdown email says so.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# These match the Terraform variable defaults. Cloud-init writes the same
# numbers into /etc/dutch-blitz/idle.env from terraform.tfvars.
DEFAULT_IDLE_WARN_MINUTES = 60
DEFAULT_IDLE_SHUTDOWN_MINUTES = 90
DEFAULT_UPTIME_WARN_MINUTES = 7 * 60
DEFAULT_UPTIME_SHUTDOWN_MINUTES = 8 * 60
DEFAULT_SNOOZE_MINUTES = 120
DEFAULT_CHECK_MINUTES = 15

BACKUP_ARGV = ["/usr/local/bin/dutch-blitz-backup", "backup"]
# Absolute path so a service with a short PATH still finds it.
# This is `shutdown -h now`. The instance attribute turns it into terminate.
SHUTDOWN_ARGV = ["/usr/sbin/shutdown", "-h", "now"]
SNOOZE_COMMAND = "sudo /usr/local/bin/dutch-blitz-idle snooze"
DOWN_COMMAND = "PYTHONPATH=src python3 -m dutch_blitz_host down"

_TOPIC_NAME = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")
_REGION = re.compile(r"[a-z]{2}-[a-z]+-\d\Z")
_ACCOUNT = re.compile(r"[0-9]{12}\Z")
_WHOLE_MINUTES = re.compile(r"[0-9]+\Z")
# A request timestamp from before this boot is ignored. 120 seconds of
# slack covers clock skew, not "the file was copied from last night".
_BOOT_SKEW_SECONDS = 120


@dataclass(frozen=True)
class Limits:
    """Minutes. Warn thresholds are lower than the matching shutdown."""

    idle_warn_minutes: float = DEFAULT_IDLE_WARN_MINUTES
    idle_shutdown_minutes: float = DEFAULT_IDLE_SHUTDOWN_MINUTES
    uptime_warn_minutes: float = DEFAULT_UPTIME_WARN_MINUTES
    uptime_shutdown_minutes: float = DEFAULT_UPTIME_SHUTDOWN_MINUTES
    snooze_minutes: float = DEFAULT_SNOOZE_MINUTES
    check_minutes: float = DEFAULT_CHECK_MINUTES


@dataclass(frozen=True)
class Snapshot:
    """What the timer observed. No clocks are read inside ``decide``."""

    uptime_minutes: float
    idle_minutes: float
    extra_uptime_minutes: float = 0
    idle_warned: bool = False
    uptime_warned: bool = False


@dataclass(frozen=True)
class Decision:
    """The action, which clocks tripped, and the flag values to store.

    ``idle_warned`` and ``uptime_warned`` already include re-arming: a
    clock that has fallen back under its warning line is false again, so
    the next crossing sends one new email.
    """

    action: str
    reasons: tuple[str, ...]
    idle_warned: bool
    uptime_warned: bool
    uptime_minutes: float
    idle_minutes: float
    shutdown_in_minutes: float
    uptime_deadline_minutes: float
    idle_shutdown_in_minutes: float
    uptime_shutdown_in_minutes: float


@dataclass(frozen=True)
class CheckOutcome:
    action: str
    state: dict
    published: bool
    shut_down: bool


def uptime_deadline_minutes(limits: Limits, extra_uptime_minutes: float) -> float:
    """Hard cap, plus every successful snooze.

    Each snooze adds ``snooze_minutes`` on top of the configured cap.
    It does not restart an 8 hour window. Two snoozes of 2 hours make
    the cap 12 hours. Idle shutdown is unchanged.
    """
    return limits.uptime_shutdown_minutes + max(0.0, extra_uptime_minutes)


def warn_lead_minutes(limits: Limits) -> float:
    """How long before the uptime cap the warning goes out.

    With the defaults this is 60 minutes (8 hours minus 7). Snooze moves
    the cap, and the warning stays this far ahead of the new cap.
    """
    return max(0.0, limits.uptime_shutdown_minutes - limits.uptime_warn_minutes)


def decide(snapshot: Snapshot, limits: Limits) -> Decision:
    """Choose none, warn, or shutdown. Shutdown wins over a warning.

    A check that is already past a limit shuts down even if the warning
    email never went out. The timer is every ``check_minutes``, and a
    missed warning must not keep a forgotten host billing.
    """
    if limits.idle_shutdown_minutes <= 0 or limits.uptime_shutdown_minutes <= 0:
        raise ValueError("shutdown limits must be positive")

    idle = max(0.0, snapshot.idle_minutes)
    uptime = max(0.0, snapshot.uptime_minutes)
    deadline = uptime_deadline_minutes(limits, snapshot.extra_uptime_minutes)
    lead = warn_lead_minutes(limits)
    uptime_warn_at = deadline - lead

    # A warn threshold that is not strictly before shutdown is not a
    # separate email. The shutdown branch covers that moment.
    idle_past_warn = (
        limits.idle_warn_minutes < limits.idle_shutdown_minutes and idle >= limits.idle_warn_minutes
    )
    uptime_past_warn = lead > 0 and uptime >= uptime_warn_at

    # Re-arm. Dropping back under the line (someone opened the app, or
    # a snooze pushed the uptime warning into the future) clears the flag.
    idle_warned = snapshot.idle_warned and idle_past_warn
    uptime_warned = snapshot.uptime_warned and uptime_past_warn

    idle_left = limits.idle_shutdown_minutes - idle
    uptime_left = deadline - uptime
    fields = {
        "uptime_minutes": uptime,
        "idle_minutes": idle,
        "shutdown_in_minutes": min(idle_left, uptime_left),
        "uptime_deadline_minutes": deadline,
        "idle_shutdown_in_minutes": idle_left,
        "uptime_shutdown_in_minutes": uptime_left,
    }

    shutdown_reasons: list[str] = []
    if idle >= limits.idle_shutdown_minutes:
        shutdown_reasons.append("idle")
    if uptime >= deadline:
        shutdown_reasons.append("uptime")
    if shutdown_reasons:
        return Decision(
            action="shutdown",
            reasons=tuple(shutdown_reasons),
            idle_warned=True,
            uptime_warned=True,
            **fields,
        )

    warn_reasons: list[str] = []
    if idle_past_warn and not idle_warned:
        warn_reasons.append("idle")
        idle_warned = True
    if uptime_past_warn and not uptime_warned:
        warn_reasons.append("uptime")
        uptime_warned = True
    if warn_reasons:
        return Decision(
            action="warn",
            reasons=tuple(warn_reasons),
            idle_warned=idle_warned,
            uptime_warned=uptime_warned,
            **fields,
        )

    return Decision(
        action="none",
        reasons=(),
        idle_warned=idle_warned,
        uptime_warned=uptime_warned,
        **fields,
    )


def format_minutes(minutes: float) -> str:
    """Whole minutes, rounded down. 90.9 is still "90 minutes"."""
    total = max(0, int(minutes))
    hours, mins = divmod(total, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} hour" if hours == 1 else f"{hours} hours")
    if mins or not parts:
        parts.append(f"{mins} minute" if mins == 1 else f"{mins} minutes")
    return " ".join(parts)


def format_utc(epoch: float) -> str:
    moment = datetime.fromtimestamp(epoch, UTC)
    return moment.strftime("%Y-%m-%d %H:%M UTC")


def _earlier_clock(decision: Decision) -> str:
    if decision.idle_shutdown_in_minutes <= decision.uptime_shutdown_in_minutes:
        return "idle"
    return "uptime"


def _reason_sentence(reasons: tuple[str, ...], limits: Limits) -> str:
    idle = f"idle for {format_minutes(limits.idle_shutdown_minutes)}"
    uptime = f"up for {format_minutes(limits.uptime_shutdown_minutes)}"
    if reasons == ("idle",):
        return f"the idle limit ({idle} with no requests)"
    if reasons == ("uptime",):
        return f"the uptime limit ({uptime}, plus any snooze)"
    if "idle" in reasons and "uptime" in reasons:
        return f"both the idle limit ({idle}) and the uptime limit ({uptime})"
    return "the configured limit"


def render_notice(
    kind: str,
    *,
    decision: Decision,
    limits: Limits,
    now: float,
    public_url: str,
    check_minutes: float,
) -> tuple[str, str]:
    """Subject and body for one SNS email.

    The only https URL in a warning is the scorekeeper. Opening it resets
    the idle clock. It does not snooze uptime and it does not shut down.
    """
    if kind not in {"warn", "shutdown", "backup_failed"}:
        raise ValueError(f"unknown notice {kind}")
    url = public_url.strip() or "(the https URL printed by up)"
    when = format_utc(now + max(decision.shutdown_in_minutes, 0) * 60)
    soon = (
        "as soon as this check finishes"
        if decision.shutdown_in_minutes <= 0
        else f"in about {format_minutes(decision.shutdown_in_minutes)} ({when})"
    )
    uptime = format_minutes(decision.uptime_minutes)
    idle = format_minutes(decision.idle_minutes)
    first = _earlier_clock(decision)
    first_line = (
        "The idle limit will be reached first. Open the site to reset it."
        if first == "idle"
        else "The uptime limit will be reached first. Snooze from Session Manager to add time. Opening the site does not move this clock."
    )
    keep_alive = (
        "To keep it running:\n"
        f"- Open {url}. Any request to the scorekeeper resets the idle timer.\n"
        f"- The uptime limit is separate. On the instance, run:\n"
        f"    {SNOOZE_COMMAND}\n"
        f"  That adds {format_minutes(limits.snooze_minutes)} to the uptime deadline. "
        "Run it again if the game is still going.\n"
        "- There is no web address that snoozes the uptime limit or shuts the server down. "
        "Port 22 is closed. Open Session Manager in the console."
    )
    afterwards = (
        "After it powers off, EC2 terminates the instance. The blitz DNS name and "
        "Terraform state still describe it until you run, from a checkout on your laptop:\n"
        f"    {DOWN_COMMAND}\n"
        "down sees that the instance is already gone, skips another backup, and "
        "removes the DNS record and the state. The next up creates a new instance."
    )
    check = format_minutes(check_minutes)

    if kind == "warn":
        subject = "Dutch Blitz will shut down soon"
        body = (
            f"The game-night server has been up for {uptime}.\n"
            f"It has been idle for {idle}.\n"
            f"It will shut itself down {soon}.\n"
            f"{first_line}\n"
            "\n"
            "Limits:\n"
            f"- idle shutdown at {format_minutes(limits.idle_shutdown_minutes)} without a request "
            f"(warning at {format_minutes(limits.idle_warn_minutes)})\n"
            f"- uptime shutdown at {format_minutes(decision.uptime_deadline_minutes)} "
            f"(warning {format_minutes(warn_lead_minutes(limits))} before that)\n"
            f"The check runs every {check}, so shutdown can be that late.\n"
            "\n"
            f"{keep_alive}\n"
            "\n"
            "This warning is sent once each time a clock crosses its line. "
            "Using the site re-arms the idle warning. Snooze re-arms the uptime warning.\n"
            "\n"
            f"{afterwards}\n"
        )
        return subject, body

    if kind == "shutdown":
        subject = "Dutch Blitz host is shutting down"
        body = (
            "The game-night server is shutting down now.\n"
            f"It was up for {uptime} and idle for {idle}.\n"
            f"Reason: {_reason_sentence(decision.reasons, limits)}.\n"
            "A score snapshot was uploaded to S3 before this email.\n"
            "\n"
            f"{afterwards}\n"
        )
        return subject, body

    subject = "Dutch Blitz backup failed, host left running"
    body = (
        "The game-night server reached a shutdown limit, but the S3 backup failed.\n"
        f"It has been up for {uptime} and idle for {idle}.\n"
        f"Reason it wanted to stop: {_reason_sentence(decision.reasons, limits)}.\n"
        "The instance is still running. Scores since the last successful upload are only on this machine.\n"
        "The next check tries the backup again and does not send this failure email a second time "
        "unless the backup starts working, or someone uses the site so the idle limit clears.\n"
        "\n"
        "From your laptop:\n"
        "    PYTHONPATH=src python3 -m dutch_blitz_host backup\n"
        "or shut it down yourself, which also tries a backup:\n"
        f"    {DOWN_COMMAND}\n"
    )
    return subject, body


def perform_check(
    snapshot: Snapshot,
    limits: Limits,
    state: dict,
    *,
    now: float,
    public_url: str,
    publish: Callable[[str, str], bool],
    backup: Callable[[], int],
    halt: Callable[[], None],
) -> CheckOutcome:
    """Run the side effects for one decision.

    ``publish`` returns true when SNS accepted the message. ``backup``
    returns a process exit code. ``halt`` is ``shutdown -h now`` and is
    called only after a backup exit code of 0. A failed final email still
    halts: the point of this timer is to stop the bill, and a missing
    subscription must not pin the instance. A failed warning email does
    not set the warned flag, so the next check tries again.
    """
    decision = decide(snapshot, limits)
    stored = _state_from(snapshot, decision, backup_failure_emailed=False)

    if decision.action == "none":
        return CheckOutcome("none", stored, False, False)

    if decision.action == "warn":
        subject, message = render_notice(
            "warn",
            decision=decision,
            limits=limits,
            now=now,
            public_url=public_url,
            check_minutes=limits.check_minutes,
        )
        published = bool(publish(subject, message))
        if not published:
            # A rejected warning is not "already sent". Flags this check
            # cleared (clock back under the line) stay cleared.
            stored = _state_from(
                snapshot,
                decision,
                backup_failure_emailed=False,
                idle_warned=_flag_after_failed_publish("idle", decision),
                uptime_warned=_flag_after_failed_publish("uptime", decision),
            )
        return CheckOutcome("warn" if published else "warn_failed", stored, published, False)

    code = backup()
    if code != 0:
        already = bool(state.get("backup_failure_emailed"))
        published = False
        if not already:
            subject, message = render_notice(
                "backup_failed",
                decision=decision,
                limits=limits,
                now=now,
                public_url=public_url,
                check_minutes=limits.check_minutes,
            )
            published = bool(publish(subject, message))
        stored = _state_from(
            snapshot,
            decision,
            backup_failure_emailed=already or published,
        )
        return CheckOutcome("backup_failed", stored, published, False)

    subject, message = render_notice(
        "shutdown",
        decision=decision,
        limits=limits,
        now=now,
        public_url=public_url,
        check_minutes=limits.check_minutes,
    )
    published = bool(publish(subject, message))
    halt()
    return CheckOutcome("shutdown", stored, published, True)


def _flag_after_failed_publish(clock: str, decision: Decision) -> bool:
    """Keep a warning unsent when SNS rejected it.

    ``decision.reasons`` lists only the clocks this check newly crossed.
    Those go back to false. A clock that was already warned stays warned.
    A clock that fell back under its line stays cleared, because that flag
    is already false and the clock is not in ``reasons``.
    """
    if clock in decision.reasons:
        return False
    if clock == "idle":
        return decision.idle_warned
    return decision.uptime_warned


def _state_from(
    snapshot: Snapshot,
    decision: Decision,
    *,
    backup_failure_emailed: bool,
    idle_warned: bool | None = None,
    uptime_warned: bool | None = None,
) -> dict:
    return {
        "extra_uptime_minutes": _json_number(snapshot.extra_uptime_minutes),
        "idle_warned": decision.idle_warned if idle_warned is None else idle_warned,
        "uptime_warned": decision.uptime_warned if uptime_warned is None else uptime_warned,
        "backup_failure_emailed": backup_failure_emailed,
    }


def _json_number(value: float) -> int | float:
    if float(value) == int(value):
        return int(value)
    return float(value)


def apply_snooze(state: dict, limits: Limits) -> dict:
    """Add one snooze block and re-arm the uptime warning.

    Idle flags are left alone. Snooze does not pretend someone opened the app.
    """
    extra = float(state.get("extra_uptime_minutes") or 0) + limits.snooze_minutes
    return {
        "extra_uptime_minutes": _json_number(max(0.0, extra)),
        "idle_warned": bool(state.get("idle_warned")),
        "uptime_warned": False,
        "backup_failure_emailed": bool(state.get("backup_failure_emailed")),
    }


def limits_from_mapping(values: dict[str, str]) -> Limits:
    """Read Terraform tfvars keys or the idle.env names.

    Both names are accepted so the controller and the host share one parser.
    Missing keys use the defaults. A bad number stops the command instead
    of being treated as zero, which would shut the host down immediately.
    """
    idle_warn = _minutes(
        values, "idle_warn_minutes", "IDLE_WARN_MINUTES", DEFAULT_IDLE_WARN_MINUTES
    )
    idle_stop = _minutes(
        values, "idle_shutdown_minutes", "IDLE_SHUTDOWN_MINUTES", DEFAULT_IDLE_SHUTDOWN_MINUTES
    )
    up_warn = _minutes(
        values, "uptime_warn_minutes", "UPTIME_WARN_MINUTES", DEFAULT_UPTIME_WARN_MINUTES
    )
    up_stop = _minutes(
        values,
        "uptime_shutdown_minutes",
        "UPTIME_SHUTDOWN_MINUTES",
        DEFAULT_UPTIME_SHUTDOWN_MINUTES,
    )
    snooze = _minutes(values, "idle_snooze_minutes", "IDLE_SNOOZE_MINUTES", DEFAULT_SNOOZE_MINUTES)
    check = _minutes(values, "idle_check_minutes", "IDLE_CHECK_MINUTES", DEFAULT_CHECK_MINUTES)
    if idle_warn < 1 or idle_warn >= idle_stop:
        raise SystemExit(
            "idle_warn_minutes must be at least 1 and less than idle_shutdown_minutes."
        )
    if up_warn < 1 or up_warn >= up_stop:
        raise SystemExit(
            "uptime_warn_minutes must be at least 1 and less than uptime_shutdown_minutes."
        )
    if not 1 <= snooze <= 24 * 60:
        raise SystemExit("idle_snooze_minutes must be from 1 to 1440.")
    if not 1 <= check <= 60:
        raise SystemExit("idle_check_minutes must be from 1 to 60.")
    return Limits(idle_warn, idle_stop, up_warn, up_stop, snooze, check)


def _minutes(values: dict[str, str], tf_key: str, env_key: str, default: int) -> int:
    raw = ""
    for key in (tf_key, env_key):
        if key in values and str(values[key]).strip() != "":
            raw = str(values[key]).strip()
            break
    if raw == "":
        return default
    if not _WHOLE_MINUTES.fullmatch(raw):
        raise SystemExit(f"{tf_key} must be a whole number of minutes.")
    return int(raw)


def idle_inventory_vars(tfvars: dict[str, str]) -> dict[str, str]:
    """Idle settings the boot script writes. No email address is included.

    The SNS subscription lives in Terraform. The instance only needs the
    topic name, which it turns into an ARN with the account id from IMDS.
    """
    limits = limits_from_mapping(tfvars)
    prefix = tfvars.get("name_prefix", "").strip() or "dutch-blitz"
    region = tfvars.get("aws_region", "").strip() or "us-east-2"
    topic = f"{prefix}-idle"
    if not re.fullmatch(r"[A-Za-z0-9-]+", prefix):
        raise SystemExit("name_prefix may only contain letters, digits, and hyphens.")
    if not _REGION.fullmatch(region):
        raise SystemExit("aws_region must look like us-east-2.")
    values = {
        "idle_warn_minutes": str(int(limits.idle_warn_minutes)),
        "idle_shutdown_minutes": str(int(limits.idle_shutdown_minutes)),
        "uptime_warn_minutes": str(int(limits.uptime_warn_minutes)),
        "uptime_shutdown_minutes": str(int(limits.uptime_shutdown_minutes)),
        "idle_snooze_minutes": str(int(limits.snooze_minutes)),
        "idle_check_minutes": str(int(limits.check_minutes)),
        "idle_topic_name": topic,
        "aws_region": region,
    }
    for key, value in values.items():
        if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
            raise SystemExit(f"{key} is not safe to write into the boot script.")
    return values


def last_request_epoch_in_log(text: str) -> float | None:
    """Newest Caddy JSON access-log timestamp, skipping ACME challenges.

    Caddy's ``ts`` is unix seconds. A millisecond or nanosecond value is
    scaled down. Lines that are not JSON are skipped so a partial write
    at the end of the file does not hide the previous request.
    """
    latest: float | None = None
    for line in text.splitlines():
        epoch = _log_line_epoch(line)
        if epoch is None:
            continue
        if latest is None or epoch > latest:
            latest = epoch
    return latest


def _log_line_epoch(line: str) -> float | None:
    stripped = line.strip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    request = payload.get("request")
    uri = ""
    if isinstance(request, dict):
        uri = str(request.get("uri") or request.get("path") or "")
    path = uri.split("?", 1)[0]
    if path.startswith("/.well-known/acme-challenge/"):
        return None
    return _normalize_epoch(payload.get("ts"))


def _normalize_epoch(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    epoch = float(value)
    if epoch > 1e14:
        epoch /= 1e9
    elif epoch > 1e11:
        epoch /= 1e3
    if epoch <= 0:
        return None
    return epoch


def latest_request_epoch(
    values: list[float | None],
    *,
    boot_epoch: float,
    skew_seconds: float = _BOOT_SKEW_SECONDS,
) -> float | None:
    """Newest request that belongs to this boot.

    The activity file and the Caddy log are created on this instance.
    A timestamp from before boot is ignored so a restored or copied file
    cannot look like a request from tonight, and cannot look like months
    of idle either. No timestamp means idle for the whole boot.
    """
    floor = boot_epoch - skew_seconds
    fresh = [value for value in values if value is not None and value >= floor]
    if not fresh:
        return None
    return max(fresh)


def idle_minutes(*, now: float, boot_epoch: float, last_request_epoch: float | None) -> float:
    anchor = boot_epoch if last_request_epoch is None else last_request_epoch
    return max(0.0, (now - anchor) / 60.0)


def uptime_minutes_from_seconds(uptime_seconds: float) -> float:
    return max(0.0, uptime_seconds / 60.0)


def read_text_tail(path: Path, max_bytes: int = 65536) -> str:
    """Last bytes of a log. A partial first line in the tail is dropped."""
    if not path.is_file():
        return ""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
            handle.readline()
        data = handle.read()
    return data.decode("utf-8", errors="replace")


def read_epoch_file(path: Path) -> float | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def read_uptime_seconds(path: Path = Path("/proc/uptime")) -> float:
    first = path.read_text(encoding="utf-8").split()[0]
    return float(first)


def load_state(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def save_state(path: Path, state: dict) -> None:
    """Atomic replace. The file stays root-owned when root runs this.

    Mode 0644 so it can be read, and not world-writable so the app user
    cannot snooze the uptime cap by editing it. The directory is 0755.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o755)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o644)
    temporary.replace(path)


def snapshot_from_state(state: dict, *, uptime_minutes: float, idle_minutes: float) -> Snapshot:
    try:
        extra = float(state.get("extra_uptime_minutes") or 0)
    except (TypeError, ValueError):
        extra = 0
    return Snapshot(
        uptime_minutes=uptime_minutes,
        idle_minutes=idle_minutes,
        extra_uptime_minutes=max(0.0, extra),
        idle_warned=bool(state.get("idle_warned")),
        uptime_warned=bool(state.get("uptime_warned")),
    )


def build_topic_arn(region: str, account_id: str, topic_name: str) -> str:
    """ARN for the topic terraform/game-role creates: ``<name_prefix>-idle``."""
    if not _REGION.fullmatch(region):
        raise ValueError("region must look like us-east-2")
    if not _ACCOUNT.fullmatch(account_id):
        raise ValueError("account id must be 12 digits")
    if not _TOPIC_NAME.fullmatch(topic_name):
        raise ValueError("topic name must be letters, digits, hyphens, or underscores")
    return f"arn:aws:sns:{region}:{account_id}:{topic_name}"


def sns_publish_argv(topic_arn: str, subject: str, message: str, region: str) -> list[str]:
    """``aws sns publish`` for one topic. The instance role allows only this."""
    if not topic_arn.startswith("arn:aws:sns:"):
        raise ValueError("topic ARN must start with arn:aws:sns:")
    if not _REGION.fullmatch(region):
        raise ValueError("region must look like us-east-2")
    return [
        "aws",
        "sns",
        "publish",
        "--region",
        region,
        "--topic-arn",
        topic_arn,
        "--subject",
        subject[:100],
        "--message",
        message,
    ]


def account_id_from_document(document: dict) -> str:
    account = str(document.get("accountId", "")).strip()
    if not _ACCOUNT.fullmatch(account):
        raise ValueError("instance identity document has no 12-digit account id")
    return account


def fetch_instance_account_id(timeout: float = 2.0) -> str:
    """IMDSv2. The account id is not in git and not in the idle env file."""
    token_request = urllib.request.Request(
        "http://169.254.169.254/latest/api/token",
        method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
    )
    with urllib.request.urlopen(token_request, timeout=timeout) as response:
        token = response.read().decode("utf-8")
    document_request = urllib.request.Request(
        "http://169.254.169.254/latest/dynamic/instance-identity/document",
        headers={"X-aws-ec2-metadata-token": token},
    )
    with urllib.request.urlopen(document_request, timeout=timeout) as response:
        document = json.loads(response.read().decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError("instance identity document is not a JSON object")
    return account_id_from_document(document)


def _boot_epoch(now: float, uptime_seconds: float) -> float:
    return now - max(0.0, uptime_seconds)


def observe(
    *,
    now: float,
    uptime_seconds: float,
    log_text: str,
    activity_epoch: float | None,
    state: dict,
) -> Snapshot:
    boot = _boot_epoch(now, uptime_seconds)
    last = latest_request_epoch(
        [last_request_epoch_in_log(log_text), activity_epoch],
        boot_epoch=boot,
    )
    return snapshot_from_state(
        state,
        uptime_minutes=uptime_minutes_from_seconds(uptime_seconds),
        idle_minutes=idle_minutes(now=now, boot_epoch=boot, last_request_epoch=last),
    )


def resolve_topic_arn(env: dict[str, str], account_id: str) -> str:
    explicit = env.get("SNS_TOPIC_ARN", "").strip()
    if explicit:
        if not explicit.startswith("arn:aws:sns:"):
            raise ValueError("SNS_TOPIC_ARN must be an SNS topic ARN")
        return explicit
    region = env.get("AWS_REGION", "").strip() or env.get("AWS_DEFAULT_REGION", "").strip()
    name = env.get("IDLE_TOPIC_NAME", "").strip() or "dutch-blitz-idle"
    return build_topic_arn(region, account_id, name)


def _state_path(env: Mapping[str, str]) -> Path:
    raw = env.get("IDLE_STATE_PATH", "").strip() or "/var/lib/dutch-blitz-idle/state.json"
    return Path(raw)


def _public_url(env: dict[str, str]) -> str:
    return env.get("IDLE_PUBLIC_URL", "").strip()


def cmd_snooze(env: dict[str, str] | None = None, *, now: float | None = None) -> int:
    values = os.environ if env is None else env
    limits = limits_from_mapping(dict(values))
    path = _state_path(values)
    state = apply_snooze(load_state(path), limits)
    save_state(path, state)
    moment = time.time() if now is None else now
    try:
        uptime = uptime_minutes_from_seconds(read_uptime_seconds())
    except OSError:
        uptime = 0
    deadline = uptime_deadline_minutes(limits, float(state["extra_uptime_minutes"]))
    remaining = max(0.0, deadline - uptime)
    print(f"Snooze added {format_minutes(limits.snooze_minutes)} to the uptime deadline.")
    print(
        f"Uptime shutdown is now at {format_minutes(deadline)} of uptime "
        f"({format_minutes(remaining)} from now, around {format_utc(moment + remaining * 60)})."
    )
    print("Opening the scorekeeper still resets only the idle timer. This command does not.")
    print("There is no URL that does what this command does.")
    return 0


def cmd_check(
    env: dict[str, str] | None = None,
    *,
    runner: Any = None,
    account_lookup: Callable[[], str] | None = None,
) -> int:
    """One timer tick. Reads local files, then maybe SNS, backup, and shutdown."""
    values = dict(os.environ if env is None else env)
    limits = limits_from_mapping(values)
    now = time.time()
    uptime_seconds = read_uptime_seconds()
    log_path = Path(values.get("IDLE_ACCESS_LOG", "").strip() or "/var/log/caddy/access.log")
    activity_path = Path(
        values.get("IDLE_ACTIVITY_PATH", "").strip() or "/var/lib/dutch-blitz/last-activity"
    )
    state_path = _state_path(values)
    state = load_state(state_path)
    snapshot = observe(
        now=now,
        uptime_seconds=uptime_seconds,
        log_text=read_text_tail(log_path),
        activity_epoch=read_epoch_file(activity_path),
        state=state,
    )
    run = runner if runner is not None else _run

    def publish(subject: str, message: str) -> bool:
        try:
            account = (
                account_lookup() if account_lookup is not None else fetch_instance_account_id()
            )
            topic = resolve_topic_arn(values, account)
            region = (
                values.get("AWS_REGION", "").strip()
                or values.get("AWS_DEFAULT_REGION", "").strip()
                or "us-east-2"
            )
            completed = run(sns_publish_argv(topic, subject, message, region), timeout=30)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            print(f"Could not send the SNS email: {exc}", file=sys.stderr)
            return False
        if completed.returncode != 0:
            print("SNS publish failed.", file=sys.stderr)
            return False
        return True

    def backup() -> int:
        print("Uploading a score snapshot before shutdown.")
        completed = run(BACKUP_ARGV, timeout=600)
        return completed.returncode

    def halt() -> None:
        print("Backup succeeded. Shutting down with shutdown -h now.")
        run(SHUTDOWN_ARGV, timeout=60)

    outcome = perform_check(
        snapshot,
        limits,
        state,
        now=now,
        public_url=_public_url(values),
        publish=publish,
        backup=backup,
        halt=halt,
    )
    save_state(state_path, outcome.state)
    print(
        f"Uptime {format_minutes(snapshot.uptime_minutes)}, "
        f"idle {format_minutes(snapshot.idle_minutes)}: {outcome.action}."
    )
    return 0


def _run(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=False, timeout=timeout, text=True)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args in ([], ["check"]):
        return cmd_check()
    if args == ["snooze"]:
        return cmd_snooze()
    print("Usage: dutch-blitz-idle check|snooze", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
