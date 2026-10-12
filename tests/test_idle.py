"""Idle and uptime decisions. No AWS, no network, no systemd."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.activity import note_activity
from app.main import create_app
from dutch_blitz_host.idle import (
    BACKUP_ARGV,
    SHUTDOWN_ARGV,
    SNOOZE_COMMAND,
    Limits,
    Snapshot,
    apply_snooze,
    build_topic_arn,
    decide,
    format_minutes,
    idle_inventory_vars,
    idle_minutes,
    last_request_epoch_in_log,
    latest_request_epoch,
    limits_from_mapping,
    observe,
    perform_check,
    read_text_tail,
    render_notice,
    sns_publish_argv,
)
from tests.fakes import FakeVerifier

LIMITS = Limits()
NOW = datetime(2026, 10, 12, 1, 0, tzinfo=timezone.utc).timestamp()


def snap(**overrides) -> Snapshot:
    base = dict(
        uptime_minutes=10,
        idle_minutes=0,
        extra_uptime_minutes=0,
        idle_warned=False,
        uptime_warned=False,
    )
    base.update(overrides)
    return Snapshot(**base)


def test_format_minutes_rounds_down():
    assert format_minutes(0) == "0 minutes"
    assert format_minutes(1) == "1 minute"
    assert format_minutes(90.9) == "1 hour 30 minutes"
    assert format_minutes(60) == "1 hour"
    assert format_minutes(61) == "1 hour 1 minute"
    assert format_minutes(7 * 60 + 2) == "7 hours 2 minutes"


def test_a_fresh_host_and_an_active_table_do_nothing():
    assert decide(snap(uptime_minutes=10, idle_minutes=10), LIMITS).action == "none"
    assert decide(snap(uptime_minutes=30, idle_minutes=59), LIMITS).action == "none"
    playing = decide(snap(uptime_minutes=3 * 60, idle_minutes=1), LIMITS)
    assert playing.action == "none"
    assert playing.reasons == ()


def test_idle_warns_once_then_rearms_when_someone_opens_the_app():
    first = decide(snap(idle_minutes=60), LIMITS)
    assert first.action == "warn"
    assert first.reasons == ("idle",)
    assert first.idle_warned is True

    again = decide(snap(idle_minutes=75, idle_warned=True), LIMITS)
    assert again.action == "none"
    assert again.idle_warned is True

    reset = decide(snap(idle_minutes=5, idle_warned=True), LIMITS)
    assert reset.action == "none"
    assert reset.idle_warned is False

    second_spell = decide(snap(idle_minutes=60, idle_warned=False), LIMITS)
    assert second_spell.action == "warn"
    assert second_spell.reasons == ("idle",)


def test_idle_shutdown_does_not_wait_for_a_warning_that_was_missed():
    missed = decide(snap(idle_minutes=90, idle_warned=False), LIMITS)
    assert missed.action == "shutdown"
    assert missed.reasons == ("idle",)

    after_warning = decide(snap(idle_minutes=95, idle_warned=True), LIMITS)
    assert after_warning.action == "shutdown"
    assert after_warning.reasons == ("idle",)


def test_uptime_warns_at_seven_hours_and_shuts_down_at_eight_while_in_use():
    almost = decide(snap(uptime_minutes=7 * 60 - 1, idle_minutes=1), LIMITS)
    assert almost.action == "none"

    warning = decide(snap(uptime_minutes=7 * 60, idle_minutes=1), LIMITS)
    assert warning.action == "warn"
    assert warning.reasons == ("uptime",)
    assert warning.uptime_shutdown_in_minutes == 60

    repeat = decide(snap(uptime_minutes=7 * 60 + 30, idle_minutes=1, uptime_warned=True), LIMITS)
    assert repeat.action == "none"

    hard = decide(snap(uptime_minutes=8 * 60, idle_minutes=0, uptime_warned=True), LIMITS)
    assert hard.action == "shutdown"
    assert hard.reasons == ("uptime",)


def test_both_clocks_at_once_shutdown_beats_a_warning():
    both_warn = decide(snap(uptime_minutes=7 * 60 + 2, idle_minutes=61), LIMITS)
    assert both_warn.action == "warn"
    assert both_warn.reasons == ("idle", "uptime")

    both_stop = decide(snap(uptime_minutes=8 * 60, idle_minutes=90), LIMITS)
    assert both_stop.action == "shutdown"
    assert both_stop.reasons == ("idle", "uptime")

    # Idle is already in the warning band and uptime has hit the cap.
    mixed = decide(snap(uptime_minutes=8 * 60, idle_minutes=70, idle_warned=True), LIMITS)
    assert mixed.action == "shutdown"
    assert mixed.reasons == ("uptime",)


def test_snooze_adds_time_to_uptime_only():
    snoozed = apply_snooze({"extra_uptime_minutes": 0, "idle_warned": True, "uptime_warned": True}, LIMITS)
    assert snoozed["extra_uptime_minutes"] == 120
    assert snoozed["uptime_warned"] is False
    assert snoozed["idle_warned"] is True

    # 7 hours up, plus a 2 hour snooze, is still under the new 9 hour warning.
    held = decide(
        snap(uptime_minutes=7 * 60, idle_minutes=1, extra_uptime_minutes=120, uptime_warned=True),
        LIMITS,
    )
    assert held.action == "none"
    assert held.uptime_warned is False
    assert held.uptime_deadline_minutes == 10 * 60

    warning = decide(snap(uptime_minutes=9 * 60, idle_minutes=1, extra_uptime_minutes=120), LIMITS)
    assert warning.action == "warn"
    assert warning.reasons == ("uptime",)

    stop = decide(snap(uptime_minutes=10 * 60, idle_minutes=1, extra_uptime_minutes=120), LIMITS)
    assert stop.action == "shutdown"
    assert stop.reasons == ("uptime",)

    # A long snooze does not keep an unused host up.
    forgotten = decide(snap(uptime_minutes=30, idle_minutes=90, extra_uptime_minutes=999), LIMITS)
    assert forgotten.action == "shutdown"
    assert forgotten.reasons == ("idle",)


def test_a_second_snooze_stacks():
    once = apply_snooze({}, LIMITS)
    twice = apply_snooze(once, LIMITS)
    assert twice["extra_uptime_minutes"] == 240
    deadline = decide(snap(uptime_minutes=11 * 60, extra_uptime_minutes=240, idle_minutes=0), LIMITS)
    assert deadline.action == "warn"
    assert deadline.uptime_deadline_minutes == 12 * 60


def test_warning_email_says_when_it_stops_and_how_to_stay_up():
    decision = decide(snap(uptime_minutes=7 * 60 + 2, idle_minutes=61), LIMITS)
    subject, body = render_notice(
        "warn",
        decision=decision,
        limits=LIMITS,
        now=NOW,
        public_url="https://blitz.example.com",
        check_minutes=15,
    )
    assert subject == "Dutch Blitz will shut down soon"
    assert len(subject) <= 100
    assert "7 hours 2 minutes" in body
    assert "1 hour 1 minute" in body
    assert "2026-10-12 01:29 UTC" in body
    assert "https://blitz.example.com" in body
    assert body.count("https://") == 1
    assert SNOOZE_COMMAND in body
    assert "no web address" in body
    assert "dutch_blitz_host down" in body
    assert "/snooze" not in body
    assert "/shutdown" not in body


def test_shutdown_and_backup_failure_emails():
    decision = decide(snap(uptime_minutes=8 * 60, idle_minutes=12), LIMITS)
    subject, body = render_notice(
        "shutdown",
        decision=decision,
        limits=LIMITS,
        now=NOW,
        public_url="https://blitz.example.com",
        check_minutes=15,
    )
    assert subject == "Dutch Blitz host is shutting down"
    assert "shutting down now" in body
    assert "uploaded to S3" in body
    assert "uptime limit" in body
    assert "already gone" in body

    failed_subject, failed = render_notice(
        "backup_failed",
        decision=decide(snap(idle_minutes=95), LIMITS),
        limits=LIMITS,
        now=NOW,
        public_url="https://blitz.example.com",
        check_minutes=15,
    )
    assert "left running" in failed_subject
    assert "still running" in failed
    assert "does not send this failure email a second time" in failed
    assert "shutting down now" not in failed


def test_perform_check_warns_once_and_retries_when_publish_fails():
    calls = {"publish": 0, "backup": 0, "halt": 0}

    def publish(_subject, _message):
        calls["publish"] += 1
        return True

    def backup():
        calls["backup"] += 1
        return 0

    def halt():
        calls["halt"] += 1

    outcome = perform_check(
        snap(idle_minutes=60),
        LIMITS,
        {},
        now=NOW,
        public_url="https://blitz.example.com",
        publish=publish,
        backup=backup,
        halt=halt,
    )
    assert outcome.action == "warn"
    assert outcome.published is True
    assert outcome.shut_down is False
    assert outcome.state["idle_warned"] is True
    assert calls == {"publish": 1, "backup": 0, "halt": 0}

    def reject(_subject, _message):
        calls["publish"] += 1
        return False

    failed = perform_check(
        snap(idle_minutes=61, uptime_minutes=7 * 60, idle_warned=True),
        LIMITS,
        outcome.state,
        now=NOW,
        public_url="https://blitz.example.com",
        publish=reject,
        backup=backup,
        halt=halt,
    )
    assert failed.action == "warn_failed"
    assert failed.state["idle_warned"] is True
    assert failed.state["uptime_warned"] is False
    assert calls["backup"] == 0
    assert calls["halt"] == 0


def test_backup_failure_does_not_shut_down_and_emails_once():
    published = []
    halted = []

    def publish(subject, message):
        published.append((subject, message))
        return True

    first = perform_check(
        snap(idle_minutes=90),
        LIMITS,
        {},
        now=NOW,
        public_url="https://blitz.example.com",
        publish=publish,
        backup=lambda: 1,
        halt=lambda: halted.append("halt"),
    )
    assert first.action == "backup_failed"
    assert first.shut_down is False
    assert first.state["backup_failure_emailed"] is True
    assert halted == []
    assert "left running" in published[0][0]

    second = perform_check(
        snap(idle_minutes=100),
        LIMITS,
        first.state,
        now=NOW,
        public_url="https://blitz.example.com",
        publish=publish,
        backup=lambda: 1,
        halt=lambda: halted.append("halt"),
    )
    assert second.action == "backup_failed"
    assert second.published is False
    assert len(published) == 1
    assert halted == []

    # Using the site clears the failure latch so a later failure can email again.
    cleared = perform_check(
        snap(idle_minutes=1),
        LIMITS,
        second.state,
        now=NOW,
        public_url="https://blitz.example.com",
        publish=publish,
        backup=lambda: 1,
        halt=lambda: halted.append("halt"),
    )
    assert cleared.action == "none"
    assert cleared.state["backup_failure_emailed"] is False


def test_shutdown_runs_only_after_a_successful_backup_even_if_email_fails():
    order = []

    def publish(_subject, _message):
        order.append("publish")
        return False

    def backup():
        order.append("backup")
        return 0

    def halt():
        order.append("halt")

    outcome = perform_check(
        snap(uptime_minutes=8 * 60, idle_minutes=2),
        LIMITS,
        {},
        now=NOW,
        public_url="https://blitz.example.com",
        publish=publish,
        backup=backup,
        halt=halt,
    )
    assert outcome.action == "shutdown"
    assert outcome.shut_down is True
    assert outcome.published is False
    assert order == ["backup", "publish", "halt"]
    assert BACKUP_ARGV[0].endswith("dutch-blitz-backup")
    assert SHUTDOWN_ARGV == ["/usr/sbin/shutdown", "-h", "now"]


def test_caddy_log_ignores_acme_and_a_partial_last_line():
    text = "\n".join(
        [
            '{"ts": 100.5, "request": {"uri": "/.well-known/acme-challenge/token"}}',
            '{"ts": 200, "request": {"uri": "/games/1"}}',
            '{"ts": 1700000000000, "request": {"uri": "/games/1?x=1"}}',
            '{"ts": 1700000000000000000, "request": {"uri": "/"}}',
            '{"ts":',
        ]
    )
    # The nanosecond line is the newest real request. The millisecond line
    # is earlier. The broken tail is ignored. ACME is ignored.
    assert last_request_epoch_in_log(text) == pytest.approx(1_700_000_000)
    assert last_request_epoch_in_log("") is None
    assert last_request_epoch_in_log('{"ts": true, "request": {"uri": "/"}}') is None


def test_requests_from_before_this_boot_do_not_count():
    boot = 1_000_000.0
    assert latest_request_epoch([boot - 10_000, None], boot_epoch=boot) is None
    assert latest_request_epoch([boot - 10_000, boot + 50], boot_epoch=boot) == boot + 50
    # No request this boot: idle equals uptime.
    assert idle_minutes(now=boot + 600, boot_epoch=boot, last_request_epoch=None) == pytest.approx(10)
    assert idle_minutes(now=boot + 600, boot_epoch=boot, last_request_epoch=boot + 300) == pytest.approx(5)


def test_observe_uses_the_newer_of_caddy_and_the_app_stamp():
    now = 10_000.0
    uptime_seconds = 1_000
    boot = now - uptime_seconds
    log = '{"ts": %s, "request": {"uri": "/"}}\n' % (boot + 100)
    # App stamp is newer than the log line.
    snapshot = observe(
        now=now,
        uptime_seconds=uptime_seconds,
        log_text=log,
        activity_epoch=boot + 400,
        state={},
    )
    assert snapshot.idle_minutes == pytest.approx((now - (boot + 400)) / 60)
    assert snapshot.uptime_minutes == pytest.approx(uptime_seconds / 60)

    only_acme = observe(
        now=now,
        uptime_seconds=uptime_seconds,
        log_text='{"ts": %s, "request": {"uri": "/.well-known/acme-challenge/x"}}\n' % (boot + 10),
        activity_epoch=None,
        state={"idle_warned": True},
    )
    assert only_acme.idle_minutes == pytest.approx(uptime_seconds / 60)
    # Idle is the whole boot, which is under the warning line, so the flag re-arms.
    assert decide(only_acme, LIMITS).idle_warned is False


def test_log_tail_drops_a_partial_first_line(tmp_path):
    path = tmp_path / "access.log"
    path.write_bytes(b"0123456789\n" + b'{"ts": 10, "request": {"uri": "/"}}\n')
    # 40 bytes from the end starts inside the first line. That partial
    # line is dropped. The JSON request after it is kept.
    assert '"ts": 10' in read_text_tail(path, max_bytes=40)
    assert "0123456789" not in read_text_tail(path, max_bytes=40)
    assert read_text_tail(tmp_path / "missing") == ""


def test_limits_accept_tfvars_or_env_names_and_reject_bad_numbers():
    assert limits_from_mapping({}).idle_shutdown_minutes == 90
    custom = limits_from_mapping({"IDLE_SHUTDOWN_MINUTES": "30", "IDLE_WARN_MINUTES": "10"})
    assert custom.idle_warn_minutes == 10
    assert custom.idle_shutdown_minutes == 30
    with pytest.raises(SystemExit):
        limits_from_mapping({"idle_warn_minutes": "90", "idle_shutdown_minutes": "90"})
    with pytest.raises(SystemExit):
        limits_from_mapping({"idle_check_minutes": "0"})
    with pytest.raises(SystemExit):
        limits_from_mapping({"uptime_warn_minutes": "nope"})


def test_inventory_vars_omit_the_alert_email():
    values = idle_inventory_vars(
        {
            "idle_warn_minutes": "45",
            "idle_shutdown_minutes": "80",
            "name_prefix": "dutch-blitz",
            "aws_region": "us-east-2",
            "alert_email": "person@example.com",
        }
    )
    assert values["idle_warn_minutes"] == "45"
    assert values["idle_shutdown_minutes"] == "80"
    assert values["idle_topic_name"] == "dutch-blitz-idle"
    assert values["aws_region"] == "us-east-2"
    assert "alert_email" not in values
    assert "person@example.com" not in values.values()
    with pytest.raises(SystemExit):
        idle_inventory_vars({"name_prefix": "bad name"})


def test_topic_arn_is_one_topic_and_publish_is_only_that_arn():
    arn = build_topic_arn("us-east-2", "123456789012", "dutch-blitz-idle")
    assert arn == "arn:aws:sns:us-east-2:123456789012:dutch-blitz-idle"
    argv = sns_publish_argv(arn, "s" * 120, "hello", "us-east-2")
    assert argv[:4] == ["aws", "sns", "publish", "--region"]
    assert argv[argv.index("--topic-arn") + 1] == arn
    assert len(argv[argv.index("--subject") + 1]) == 100
    with pytest.raises(ValueError):
        build_topic_arn("us-east-2", "123", "dutch-blitz-idle")
    with pytest.raises(ValueError):
        build_topic_arn("us-east-2", "123456789012", "bad/name")
    with pytest.raises(ValueError):
        sns_publish_argv("arn:aws:s3:::bucket", "subject", "body", "us-east-2")


def test_a_request_stamps_activity_beside_the_database(tmp_path):
    database = tmp_path / "scores.sqlite"
    application = create_app(str(database), verifier=FakeVerifier())
    with TestClient(application) as client:
        client.cookies.set("__session", "user:user_test")
        assert client.get("/").status_code == 200
        assert client.get("/static/style.css").status_code == 200
    stamp = (tmp_path / "last-activity").read_text(encoding="utf-8")
    assert float(stamp) > 0

    # A stamp that cannot be written must not raise into the request.
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    note_activity(str(database), override=str(blocker / "last-activity"))
