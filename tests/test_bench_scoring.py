from pager_zero.bench.runner import plan_incidents, summarise
from pager_zero.bench.scoring import culprit_named, score
from pager_zero.synth import generate, list_scenarios


def test_plan_covers_all_families_round_robin():
    plan = plan_incidents(20, 7, None)
    assert len(plan) == 20 and {f for f, _ in plan} == set(list_scenarios())
    assert len({s for _, s in plan}) == 20  # distinct seeds


def test_score_correct_run():
    w = generate("bad-deploy", 42)
    gt = w.ground_truth
    state = {
        "status": "resolved",
        "steps": 5,
        "diagnosis": {
            "family": "bad-deploy",
            "root_cause": f"deploy {gt.culprit['deploy']} commit {gt.culprit['commit']} N+1",
            "evidence": [],
        },
        "plan": {"action": "rollback_deploy", "target": dict(gt.fix_target)},
        "verification": {"recovered": True},
        "messages": [],
    }
    s = score("bad-deploy", 42, {"id": "x"}, gt, state, 12.3)
    assert s.root_cause_ok and s.culprit_ok and s.fix_ok and s.verified


def test_score_wrong_family_and_fix():
    gt = generate("cache-eviction", 7).ground_truth
    state = {
        "status": "unresolved",
        "steps": 12,
        "diagnosis": {"family": "bad-deploy", "root_cause": "some deploy", "evidence": []},
        "plan": {
            "action": "rollback_deploy",
            "target": {"service": "offer-service", "to_version": "v1.0"},
        },
        "verification": {"recovered": False},
    }
    s = score("cache-eviction", 7, {"id": "x"}, gt, state, 1)
    assert not s.root_cause_ok and not s.culprit_ok and not s.fix_ok and not s.verified
    assert not culprit_named(gt, None)


def test_summarise_percentages():
    gt = generate("hot-partition", 1).ground_truth
    good = score(
        "hot-partition",
        1,
        {"id": "a"},
        gt,
        {
            "status": "resolved",
            "diagnosis": {
                "family": "hot-partition",
                "root_cause": f"{gt.culprit['key']} on {gt.culprit['table']}",
                "evidence": [],
            },
            "plan": {"action": "rate_limit_key", "target": dict(gt.fix_target)},
            "verification": {"recovered": True},
        },
        2,
    )
    bad = score("hot-partition", 2, {"id": "b"}, gt, {"status": "error"}, 1, error="boom")
    r = summarise([good, bad], 1, False)
    assert r["total"]["n"] == 2 and r["total"]["root_cause_ok"] == 1 and r["total"]["errors"] == 1
    assert r["by_family"]["hot-partition"]["fix_ok"] == 1
