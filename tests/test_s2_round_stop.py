"""One authorized round stops after its evaluation, with no next-round work."""

from recoveragent.coevolve_llm import REVISION
from recoveragent.io import read_json
from recoveragent.s2_experiment import Experiment, evaluate
from recoveragent.v2_runtime import S2_GENERATION_FOCUS_EXECUTION
from test_s2_experiment import cfg, FakeRuntime


def test_one_round_stops_after_dev_without_fifth_generation_window(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("RECOVERAGENT_S2_EXECUTION", S2_GENERATION_FOCUS_EXECUTION)
    config = cfg()
    runtime = FakeRuntime(config)
    runtime.prepare_rollouts = lambda actor: None
    bundle = {
        "pool": [],
        "scoring_revision": REVISION,
        "archive_sha256": "source",
        "feedback": [],
    }
    baseline = evaluate(
        FakeRuntime(config), "S0", config["dev_task_ids"], tmp_path / "baseline"
    )
    output = tmp_path / "evolve"
    run = Experiment(config, bundle, {"s0": "S0"}, output, "evolve", runtime)
    state = run.run(baseline)
    assert len(state["groups"]) == 40
    assert len(runtime.calls) == 160 + 24
    assert len(state["updates"]) == 20 and state["updated_through"] == 40
    assert set(state["round_evaluations"]) == {"0"}
    assert set(state["background_jobs"]) == {"8", "16", "24", "32"}
    assert (
        read_json(output / "generation/student_finished.json")["completed_groups"] == 40
    )
    resumed = Experiment(config, bundle, {"s0": "S0"}, output, "evolve", runtime)
    resumed.run(baseline)
    assert len(runtime.calls) == 184


def test_supervisor_new_directory_selects_evolve_only(tmp_path):
    from scripts.supervise_s2_background import continuation_mode

    assert (
        continuation_mode(tmp_path, {"finish_static_after_evolve": False}) == "evolve"
    )
