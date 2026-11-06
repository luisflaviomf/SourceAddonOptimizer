from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2] / "companion" / "sourceaddonoptimizer_companion"


def read(relative: str) -> str:
    return (ROOT / "lua" / "sourceaddonoptimizer" / relative).read_text(encoding="utf-8")


def observation_receiver() -> str:
    source = read("server/profiler.lua")
    return source.split("net.Receive(SAO.Constants.Net.Observation", 1)[1].split(")\n\nhook.Add", 1)[0]


@pytest.mark.parametrize(
    ("scenario", "required"),
    (
        ("unauthorized", ("SAO.Permissions.CanUse(ply)",)),
        ("replayed", ("SAO.Permissions.ValidateSession(ply, identifier)", "not state.active", "state.owner ~= ply")),
        ("expired", ("SysTime() > session.expires_at", 'record_rejection("validate_session", "expired")')),
        ("oversized", ("MAX_OBSERVATION_MESSAGE_BITS", "invalid message length", "MAX_NET_IDENTIFIER_BYTES", "MAX_OBSERVATIONS_PER_MESSAGE", "MAX_PATH_BYTES")),
        ("rate-limited", ('CheckRateLimit(ply, "profile_observation"',)),
        ("malformed", ("identifier_bytes == 0", "batch_count == 0", "path_bytes == 0", "invalid observation")),
        ("wrong-order", ("not state.active", "state.owner ~= ply")),
    ),
)
def test_observation_protocol_fails_closed_for_each_hostile_case(
    scenario: str, required: tuple[str, ...]
) -> None:
    sources = read("server/profiler.lua") + read("server/permissions.lua") + read("shared/constants.lua")
    for token in required:
        assert token in sources, (scenario, token)


def test_observation_receiver_checks_authority_size_rate_session_and_order_before_mutation() -> None:
    receiver = observation_receiver()
    authority = receiver.index("SAO.Permissions.CanUse(ply)")
    message_size = receiver.index("MAX_OBSERVATION_MESSAGE_BITS")
    rate = receiver.index("SAO.Permissions.CheckRateLimit")
    first_read = receiver.index("net.ReadUInt")
    session = receiver.index("SAO.Permissions.ValidateSession")
    mutation = receiver.index("SAO.ServerProfiler.PromoteObservation")
    assert authority < message_size < rate < first_read < session < mutation


def test_session_replay_and_expiry_remove_only_the_senders_session() -> None:
    permissions = read("server/permissions.lua")
    profiler = read("server/profiler.lua")
    stop = profiler.split("function SAO.ServerProfiler.StopAndExport", 1)[1].split("util.AddNetworkString", 1)[0]
    assert stop.index("SAO.Permissions.CloseSession(ply, identifier)") < stop.index("state.active = false")
    expired = permissions.split("if SysTime() > session.expires_at", 1)[1].split("return true", 1)[0]
    assert "sessions[key] = nil" in expired
    assert "sessions = {}" not in expired


def test_client_session_messages_are_bounded_and_wrong_order_is_ignored() -> None:
    client = read("client/profiler.lua")
    assert client.count("MAX_SESSION_MESSAGE_BITS") >= 2
    begin = client.split("net.Receive(SAO.Constants.Net.BeginSession", 1)[1].split("net.Receive(SAO.Constants.Net.EndSession", 1)[0]
    end = client.split("net.Receive(SAO.Constants.Net.EndSession", 1)[1].split("hook.Add", 1)[0]
    assert "if active_identifier then return end" in begin
    assert "if not active_identifier then return end" in end


def test_hostile_companion_messages_never_disconnect_or_punish_any_player() -> None:
    sources = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "lua" / "sourceaddonoptimizer" / "server").rglob("*.lua"))
    for forbidden in (":Kick(", ":Ban(", "game.KickID", "DropClient", "RunConsoleCommand"):
        assert forbidden not in sources
