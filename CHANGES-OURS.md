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
