# CHANGES-OURS

What this fork (`ours`, from upstream v0.3.14 `85c3961`) does differently from upstream. One entry per change:
what it is, why, the files, and its tests.

## 1. Playback answers the agent's model calls from the record

**What.** A fork brings its agent back by playing its recorded inputs into a fresh sandbox up to the checkpoint.
Upstream answers the agent's *service* calls from the record during that playback, but loads only the model calls
made *after* the checkpoint (`session.fork`: `spans(after_wake=forked_after)`), so a model call the agent makes
while being played back finds no recorded answer and goes to the model. A model asked again words its answer
differently, the agent's next service call (the mail it sends) then differs from the record's, and the fork is
refused: "the fork's agent did not do what it did before the checkpoint". With a deterministic stand-in model
this never shows; with a real model (measured: 10 identical requests at temperature 0, 9 to 10 different
answers) every fork of an LLM agent was refused (6 of 6).

Here the model calls placed in the wakes up to the checkpoint are loaded too, as a second, frozen `ModelReplay`
(`ProxyAddon.playback_model_replay`), which answers model calls while a playback is in progress (`play_back` to
`live`) and is dropped when the checkpoint is reached. A call it does not hold is refused, never sent on.

**Files.** `ports/store.py`, `adapters/store/sqlite.py`, `application/cases.py` (`spans(until_wake=)`),
`session.py` (`fork`), `adapters/proxy/addon.py` (`playback_model_replay`, `_replayed`, `live`).

**Tests.** `tests/proxy/test_playback_model_calls.py`, `tests/test_store_spans.py`
(`test_spans_are_read_up_to_a_wake_by_the_store`). Mutations tried: answering from `model_replay` during a
playback (the playback test fails: the model's new words reach the agent), and `wake<` for `wake<=` (the store
test fails).

**Cost.** The parent's model calls up to the checkpoint are held in memory during the playback and released at
the checkpoint: a later fork point holds more.

## 2. A fork may make every model call again (`Fork.live`, `minutehand fork --live`)

**What.** Upstream answers a fork's model calls after the checkpoint from the parent's record until the agent asks
something the parent did not, and a person's words from any run in the same world file that asked the same
(`Store.written`). So a fork with no change copies its parent word for word, whatever its seed: measured, 3 control
forks of one checkpoint were identical. An attack fork changes what the agent is asked, parts at once and calls the
model live. The two lines then differ in more than the attack. With `live: true` (or `--live`) the fork holds no
recorded model answer after the checkpoint (`ModelReplay` built from nothing), and its store answers `written` None
(`SqliteStore.fork(afresh=True)`), so every model call, the agent's, the people's and the judges', is made again.
Up to the checkpoint nothing changes: the playback still answers from the record (change 1). `live` and `--frozen`
together are refused.

**Files.** `domain/experiment.py` (`Fork.live`), `ports/store.py`, `adapters/store/sqlite.py`, `application/cases.py`
(`fork(afresh=)`), `application/rewind.py`, `session.py`, `cli.py` (`--live`).

**Tests.** `tests/orchestrator/test_fork_adds_a_happening.py::test_a_fork_made_afresh_is_answered_nothing_from_the_file_and_one_not_is`.
Mutation tried: `written` ignoring `afresh` (fails).

## 3. A fork may add a happening (`add_happening`)

**What.** Upstream's overrides change what is (a person's behaviour, a memory, a deadline); none adds something
that happens. `AddHappening` adds any `Happening` a scenario may hold (a Slack post, a document edited, a ticket
moved), `after` the checkpoint's moment. It is put at the end of the scenario's `happenings`, so every happening the
parent scheduled keeps its index (a checkpoint's `PendingHappening` names one by index), its `after` rewritten from
the scenario's start, and booked as pending at the checkpoint. The happening is written without its own `after` or
`gap`; one that has either is refused, and so is a person not in the scenario.

```yaml
overrides:
  - kind: add_happening
    after: PT30M
    happening: {kind: posts, provider: slack, person: sam, text: "..."}
```

**Files.** `domain/experiment.py` (`AddHappening`), `application/rewind.py` (`changed_scenario(forked_at=)`,
`_with_added`), `session.py` (`_forked_at`).

**Tests.** `tests/orchestrator/test_fork_adds_a_happening.py`: fires its `after` past the checkpoint; the parent's own
happening keeps its moment; the log up to the checkpoint is the parent's event for event and the parent is not
written to; no added happening plays as before; several fire each at its moment; refusals. Mutations tried: due at
the checkpoint itself (2 fail), added first instead of last (3 fail).
