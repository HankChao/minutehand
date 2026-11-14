"""A fork's agent played back to its checkpoint has its model calls answered from the parent's record of them
(`ProxyAddon.playback_model_replay`), not by the model: a model asked again words its answer differently, the
agent's next call then differs from the record's, and the fork is refused. Ours (CHANGES-OURS.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from minutehand.adapters.proxy.policy import Routing
from minutehand.adapters.proxy.registry import Registry
from minutehand.adapters.proxy.replay import PARTED_STATUS, ModelReplay
from minutehand.adapters.proxy.server import Proxy
from minutehand.adapters.store.sqlite import SqliteStore
from minutehand.application.run_clock import RunClock
from minutehand.application.trace import TraceReplay
from minutehand.domain.telemetry import SpanSource
from tests.proxy.support import client
from tests.proxy.upstream import Answer, Authority, make_authority, model_api

HOST = "localhost"
PATH = "/v1/chat/completions"


def _asked(text: str) -> bytes:
    return json.dumps({"model": "model-luna", "messages": [{"role": "user", "content": text}]}).encode()


def _said(text: str) -> bytes:
    return json.dumps(
        {"object": "chat.completion", "choices": [{"message": {"role": "assistant", "content": text}}]}
    ).encode()


@pytest.fixture
def authority(tmp_path: Path) -> Authority:
    return make_authority(tmp_path / "upstream-ca")


async def test_a_played_back_agents_model_calls_are_answered_from_the_record_and_the_model_is_not_asked(
    registry: Registry, store: SqliteStore, clock: RunClock, tmp_path: Path, authority: Authority
) -> None:
    routing = Routing(registry, model_hosts=[HOST])
    answer = Answer("application/json", [_said("then")])
    async with (
        model_api(authority, answer) as upstream,
        Proxy(
            routing, store, clock, confdir=tmp_path / "ca", upstream_ca=authority.ca_cert, record_model_calls=True
        ) as proxy,
        client(proxy, proxy.ca_cert) as http,
    ):

        async def ask(text: str) -> tuple[int, str]:
            response = await http.post(
                f"https://{HOST}:{upstream.port}{PATH}",
                content=_asked(text),
                headers={"content-type": "application/json"},
            )
            said = response.json()
            return response.status_code, said["choices"][0]["message"]["content"] if "choices" in said else ""

        assert [await ask("plan"), await ask("act")] == [(200, "then"), (200, "then")]  # the parent's wake 0
        recorded, reached = len(store.spans()), len(upstream.received)
        clock.begin_wake()
        answer.chunks[:] = [_said("now")]  # the model would word it differently today

        proxy.addon.model_replay = ModelReplay.of("run1", store.spans(source=SpanSource.WIRE, after_wake=0))
        proxy.addon.playback_model_replay = ModelReplay.of(
            "run1", store.spans(source=SpanSource.WIRE, until_wake=0), frozen=True
        )
        proxy.addon.play_back(TraceReplay.of("run1", []))
        assert [await ask("plan"), await ask("act")] == [(200, "then"), (200, "then")]
        assert len(upstream.received) == reached, "nothing reached the model while the agent was played back"
        assert len(store.spans()) == recorded, "the parent's record of the calls is the fork's already"
        assert (await ask("something the record does not hold"))[0] == PARTED_STATUS
        assert len(upstream.received) == reached

        proxy.addon.live()
        assert proxy.addon.playback_model_replay is None
        assert await ask("plan") == (200, "now"), "past the checkpoint a call the parent never made goes to the model"
        assert len(upstream.received) == reached + 1
