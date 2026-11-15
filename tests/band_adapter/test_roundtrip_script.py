"""scripts/band_roundtrip.py: everything that can be checked without keys or a network."""

import argparse
import asyncio
import importlib.util
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from plan_my_stay.enums import MessageType, Seat
from plan_my_stay.messages import build_message, parse_message, serialize

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "band_roundtrip.py"


@pytest.fixture(scope="module")
def rt():
    spec = importlib.util.spec_from_file_location("band_roundtrip", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["band_roundtrip"] = module
    spec.loader.exec_module(module)
    return module


def ack_for(rt, probe, *, sha=None, code=None, reply_to=None):
    """What Routing's AckSeat would answer, optionally corrupted."""
    ack = rt.AckSeat(Seat.ROUTING).handle(probe)[0]
    if sha is None and code is None and reply_to is None:
        return ack
    payload = ack.payload.model_copy(update={
        "code": code or ack.payload.code,
        "fields": (f"sha256:{sha}", "bytes:1") if sha else ack.payload.fields,
    })
    return build_message(
        type=MessageType.ERROR, sender=Seat.ROUTING, to=[Seat.CONCIERGE], trip_id=probe.trip_id, user_id=probe.user_id,
        state_version=probe.state_version, summary="x", reply_to=reply_to or probe.message_id, payload=payload,
    )


def queue_feeding(items):
    """A send() that makes `items` arrive in the queue, like BAND delivering the answers."""
    queue: asyncio.Queue = asyncio.Queue()

    async def send(body: str) -> None:
        for item in items:
            queue.put_nowait(item)

    return send, queue


# --- probes ---------------------------------------------------------------------------------------


def test_probes_are_validated_messages_from_the_concierge_to_routing_with_distinct_ids(rt):
    probes = rt.build_probes()
    assert set(probes) == {"request", "proposal", "plan_for_review"}
    assert len({p.message_id for p in probes.values()}) == 3
    for probe in probes.values():
        assert probe.sender is Seat.CONCIERGE and probe.to == (Seat.ROUTING,)
        assert parse_message(serialize(probe)) == probe


def test_the_largest_probe_is_the_merged_plan(rt):
    sizes = {name: len(serialize(p).encode()) for name, p in rt.build_probes().items()}
    assert max(sizes, key=sizes.get) == "plan_for_review"
    assert sizes["plan_for_review"] > 4000


@pytest.mark.parametrize("size", [1000, 1001, 4000, 16384, 70000])
def test_padded_probe_is_a_valid_message_of_exactly_the_requested_size(rt, size):
    probe = rt.padded_probe(size)
    body = serialize(probe)
    assert len(body.encode()) == size
    assert parse_message(body) == probe
    assert probe.sender is Seat.CONCIERGE and probe.to == (Seat.ROUTING,)


def test_padded_probe_refuses_a_size_smaller_than_the_bare_message(rt):
    with pytest.raises(ValueError, match="at least"):
        rt.padded_probe(10)


# --- the receiving seat -------------------------------------------------------------------------------


def test_ack_seat_answers_with_a_validated_error_carrying_the_digest_of_what_it_parsed(rt):
    probe = rt.build_probes()["request"]
    (ack,) = rt.AckSeat(Seat.ROUTING).handle(probe)
    body = serialize(probe)
    assert ack.type is MessageType.ERROR and ack.payload.code == rt.PROBE_ACK
    assert ack.reply_to == probe.message_id and ack.to == (Seat.CONCIERGE,)
    sha, size = rt.parse_ack_fields(ack.payload.fields)
    assert sha == rt.digest(body) and size == len(body.encode())
    assert parse_message(serialize(ack)) == ack


def test_parse_ack_fields_tolerates_missing_and_odd_fields(rt):
    assert rt.parse_ack_fields(()) == (None, None)
    assert rt.parse_ack_fields(("sha256:ab", "bytes:12")) == ("ab", 12)
    assert rt.parse_ack_fields(("bytes:x", "junk")) == (None, None)


async def test_capture_seat_hands_messages_to_the_loop_from_a_worker_thread(rt):
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    seat = rt.CaptureSeat(loop, queue)
    probe = rt.build_probes()["request"]
    thread = threading.Thread(target=lambda: seat.handle(probe))
    thread.start()
    thread.join()
    assert await asyncio.wait_for(queue.get(), 1) == ("message", probe)
    assert seat.handle(probe) == []  # it never answers, so nothing can loop


# --- reading the answers --------------------------------------------------------------------------------


async def test_a_matching_digest_means_the_message_arrived_intact(rt):
    probe = rt.build_probes()["proposal"]
    send, queue = queue_feeding([("message", ack_for(rt, probe))])
    result = await rt.probe_message(send, queue, "proposal", probe, timeout=1)
    assert result.ok and "intact" in result.detail
    assert f"{len(serialize(probe).encode())} bytes" in result.label


async def test_a_different_digest_is_reported_as_a_difference(rt):
    probe = rt.build_probes()["proposal"]
    send, queue = queue_feeding([("message", ack_for(rt, probe, sha="0" * 64))])
    result = await rt.probe_message(send, queue, "proposal", probe, timeout=1)
    assert not result.ok and "DIFFERENT" in result.detail


async def test_a_receiver_rejection_is_reported_with_its_code(rt):
    probe = rt.build_probes()["request"]
    send, queue = queue_feeding([("message", ack_for(rt, probe, code="invalid_json"))])
    result = await rt.probe_message(send, queue, "request", probe, timeout=1)
    assert not result.ok and "rejected" in result.detail and "invalid_json" in result.detail


async def test_unrelated_traffic_is_skipped_while_waiting_for_the_answer(rt):
    probe, other = rt.build_probes()["request"], rt.build_probes()["proposal"]
    items = [("text", "chatter"), ("message", probe), ("message", ack_for(rt, other)), ("message", ack_for(rt, probe))]
    send, queue = queue_feeding(items)
    assert (await rt.probe_message(send, queue, "request", probe, timeout=1)).ok


async def test_no_answer_times_out(rt):
    probe = rt.build_probes()["request"]
    send, queue = queue_feeding([])
    result = await rt.probe_message(send, queue, "request", probe, timeout=0.05)
    assert not result.ok and "no answer" in result.detail


async def test_a_refused_send_is_an_observation_not_a_crash(rt):
    async def refuse(body: str) -> None:
        raise RuntimeError("HTTP 413")

    result = await rt.probe_message(refuse, asyncio.Queue(), "request", rt.build_probes()["request"], timeout=1)
    assert not result.ok and "refused" in result.detail and "413" in result.detail


async def test_a_padded_probe_is_read_like_any_other_message(rt):
    probe = rt.padded_probe(5000)
    send, queue = queue_feeding([("message", ack_for(rt, probe))])
    result = await rt.probe_message(send, queue, "padded error", probe, timeout=1)
    assert result.ok and "5000 bytes" in result.label


# --- registration check (no network: every entry is missing) -----------------------------------------


def test_same_account_needs_all_six_under_one_owner(rt):
    me = lambda owner: SimpleNamespace(owner_uuid=owner)  # noqa: E731
    six_one = {seat: me("o1") for seat in Seat}
    assert rt.same_account(six_one).ok
    assert not rt.same_account({**six_one, Seat.GYM: me("o2")}).ok
    assert "only 1 of 6" in rt.same_account({Seat.GYM: me("o1")}).detail


async def test_registration_check_reports_every_missing_entry_without_touching_the_network(rt, directory, tmp_path):
    pytest.importorskip("band")
    config = tmp_path / "agent_config.yaml"
    config.write_text('concierge:\n  agent_id: "x"\n', encoding="utf-8")  # no api_key
    rows, identities = await rt.check_registration(config, directory, "https://band.invalid")
    assert len(rows) == 6 and not any(r.ok for r in rows) and identities == {}
    assert all("not configured" in r.detail for r in rows)


def test_api_error_text_never_includes_the_exception_string(rt):
    err = SimpleNamespace(status_code=422, body=SimpleNamespace(error=SimpleNamespace(message="too long")))
    assert rt._api_error(err) == "HTTP 422: too long"
    assert rt._api_error(SimpleNamespace(status_code=500, body=None)) == "HTTP 500"
    assert rt._api_error(ValueError("secret headers here")) == "ValueError"


# --- command line ---------------------------------------------------------------------------------------


def test_probe_sizes_are_parsed_and_bounded(rt):
    assert rt._sizes("1000, 16000,") == [1000, 16000]
    assert rt._sizes("") == []
    for bad in ("abc", "999", "3000000"):
        with pytest.raises(argparse.ArgumentTypeError):
            rt._sizes(bad)


def test_help_runs_without_the_sdk_or_keys(rt, capsys):
    with pytest.raises(SystemExit) as exit_info:
        rt.main(["--help"])
    assert exit_info.value.code == 0
    assert "--probe-sizes" in capsys.readouterr().out


def test_a_missing_seat_directory_is_a_clean_error_exit(rt, tmp_path, capsys):
    pytest.importorskip("band")
    assert rt.main(["--seats", str(tmp_path / "nope.yaml"), "--config", str(tmp_path / "c.yaml")]) == 2
    assert "nope.yaml" in capsys.readouterr().err


def test_a_missing_config_file_is_a_clean_error_exit(rt, tmp_path, capsys):
    pytest.importorskip("band")
    seats = Path(__file__).resolve().parents[2] / "band_seats.yaml"
    assert rt.main(["--seats", str(seats), "--config", str(tmp_path / "missing.yaml")]) == 2
    assert "Config file not found" in capsys.readouterr().err


def test_the_script_never_prints_or_logs_a_key():
    for number, line in enumerate(SCRIPT.read_text(encoding="utf-8").splitlines(), 1):
        if "print(" in line or "logger." in line:
            assert "api_key" not in line, f"line {number} could expose a key"


def test_probe_messages_json_is_plain_json_the_receiver_can_parse(rt):
    for probe in rt.build_probes().values():
        assert isinstance(json.loads(serialize(probe)), dict)
