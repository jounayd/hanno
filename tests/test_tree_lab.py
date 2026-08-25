"""tree_lab skill script: the searcher must return genuinely graceful
labelings (verified, not asserted), the generator must be deterministic per
seed, and malformed input must fail cleanly."""
import json
import subprocess
import sys

PY = sys.executable
SCRIPT = "skills/scripts/tree_lab.py"


def run_lab(payload: dict) -> dict:
    proc = subprocess.run(
        [PY, SCRIPT], input=json.dumps(payload),
        capture_output=True, text=True, timeout=30,
    )
    return json.loads(proc.stdout)


def test_find_labeling_on_a_path():
    out = run_lab({"op": "find_labeling", "edges": [["a", "b"], ["b", "c"], ["c", "d"], ["d", "e"]]})
    assert out["found"] is True
    assert out["verdict"]["graceful"] is True


def test_find_labeling_on_a_star():
    edges = [["c", f"leaf{i}"] for i in range(5)]
    out = run_lab({"op": "find_labeling", "edges": edges})
    assert out["found"] is True
    assert out["verdict"]["graceful"] is True


def test_find_labeling_on_a_caterpillar():
    # spine a-b-c-d with legs off b and c
    edges = [["a", "b"], ["b", "c"], ["c", "d"], ["b", "x"], ["c", "y"]]
    out = run_lab({"op": "find_labeling", "edges": edges})
    assert out["found"] is True
    assert out["verdict"]["graceful"] is True
    assert sorted(out["verdict"]["edge_labels"]) == [1, 2, 3, 4, 5]


def test_find_labeling_rejects_non_tree():
    out = run_lab({"op": "find_labeling", "edges": [["a", "b"], ["b", "c"], ["c", "a"]]})  # cycle
    assert "not a tree" in out["error"]


def test_find_labeling_zero_budget_reports_budget_exhausted():
    edges = [["a", "b"], ["b", "c"], ["c", "d"]]
    out = run_lab({"op": "find_labeling", "edges": edges, "budget_ms": 0})
    assert out["found"] is False
    assert "budget" in out["reason"]


def test_random_trees_deterministic_and_well_formed():
    payload = {"op": "random_trees", "n": 9, "count": 5, "seed": 7}
    out1 = run_lab(payload)
    out2 = run_lab(payload)
    assert out1 == out2  # same seed, same trees
    assert len(out1["trees"]) == 5
    for tree in out1["trees"]:
        assert len(tree["edges"]) == 8  # n-1 edges

    # and every generated tree must actually be searchable/graceful-checkable
    first = out1["trees"][0]["edges"]
    found = run_lab({"op": "find_labeling", "edges": first})
    assert found["found"] is True
    assert found["verdict"]["graceful"] is True


def test_check_labeling_op():
    out = run_lab({
        "op": "check_labeling",
        "edges": [["a", "b"], ["b", "c"], ["c", "d"]],
        "labels": {"a": 0, "b": 3, "c": 1, "d": 2},
    })
    assert out["graceful"] is True


def test_unknown_op_and_bad_json_fail_cleanly():
    out = run_lab({"op": "make_coffee"})
    assert "unknown op" in out["error"]

    proc = subprocess.run(
        [PY, SCRIPT], input="not json at all",
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 2
    assert "bad input" in json.loads(proc.stdout)["error"]
