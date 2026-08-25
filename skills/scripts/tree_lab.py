#!/usr/bin/env python3
"""tree_lab — search and generation instruments for graceful-labeling work.

Safe by construction: stdlib only, parses JSON, never evaluates model text
as code. Three operations, selected by the "op" field of the stdin JSON:

  {"op": "find_labeling", "edges": [[u,v],...], "budget_ms": 8000}
      Backtracking search for a graceful labeling of the given tree.
      Returns the labeling found, or found=false with the reason
      ("budget exhausted" vs "search space exhausted").

  {"op": "random_trees", "n": 10, "count": 20, "seed": 42}
      `count` uniformly random labeled trees on n vertices via Prüfer
      sequences (deterministic for a given seed). Vertices are "0".."n-1".

  {"op": "check_labeling", "edges": [[u,v],...], "labels": {"v": int}}
      Verify a specific labeling (same verdict shape as graceful_check).
"""
import json
import random
import sys
import time

MAX_N = 64
MAX_COUNT = 100
DEFAULT_BUDGET_MS = 8000


def parse_edges(raw):
    edges = [(str(u), str(v)) for u, v in raw]
    vertices = sorted({x for e in edges for x in e})
    return edges, vertices


def build_adjacency(edges, vertices):
    adj = {v: [] for v in vertices}
    for u, v in edges:
        adj[u].append(v)
        adj[v].append(u)
    return adj


def is_tree(edges, vertices):
    if not vertices or len(edges) != len(vertices) - 1:
        return False
    adj = build_adjacency(edges, vertices)
    seen = set()
    stack = [vertices[0]]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(adj[cur])
    return len(seen) == len(vertices)


def check_labeling(edges, labels):
    vertices = sorted({x for e in edges for x in e} | set(labels.keys()))
    n = len(vertices)
    missing = [v for v in vertices if v not in labels]
    if missing:
        return {"error": f"vertices with no label: {missing}"}
    label_values = sorted(labels.values())
    edge_labels = sorted(abs(labels[u] - labels[v]) for u, v in edges)
    tree = is_tree(edges, vertices)
    labels_valid = label_values == list(range(n))
    edge_labels_valid = edge_labels == list(range(1, n))
    return {
        "n_vertices": n, "n_edges": len(edges), "is_tree": tree,
        "labels_valid": labels_valid, "edge_labels": edge_labels,
        "edge_labels_valid": edge_labels_valid,
        "graceful": bool(tree and labels_valid and edge_labels_valid),
    }


def find_labeling(edges, budget_ms):
    edges, vertices = parse_edges(edges)
    if not is_tree(edges, vertices):
        return {"error": "input graph is not a tree (must be connected with n-1 edges)"}
    n = len(vertices)
    if n > MAX_N:
        return {"error": f"tree too large: {n} vertices (max {MAX_N})"}
    adj = build_adjacency(edges, vertices)

    # BFS order from a max-degree root: each vertex after the first has at
    # least one already-labeled neighbor, so pruning bites immediately.
    root = max(vertices, key=lambda v: len(adj[v]))
    order, seen = [root], {root}
    i = 0
    while i < len(order):
        for nb in adj[order[i]]:
            if nb not in seen:
                seen.add(nb)
                order.append(nb)
        i += 1

    deadline = time.monotonic() + budget_ms / 1000.0
    assignment: dict = {}
    used_vertex = set()
    used_edge = set()

    class Budget(Exception):
        pass

    def backtrack(idx):
        if time.monotonic() > deadline:
            raise Budget
        if idx == n:
            return True
        v = order[idx]
        for label in range(n):
            if label in used_vertex:
                continue
            new_edge_labels = [abs(label - assignment[u]) for u in adj[v] if u in assignment]
            if len(set(new_edge_labels)) != len(new_edge_labels):
                continue
            if any(e in used_edge for e in new_edge_labels):
                continue
            assignment[v] = label
            used_vertex.add(label)
            used_edge.update(new_edge_labels)
            if backtrack(idx + 1):
                return True
            del assignment[v]
            used_vertex.discard(label)
            used_edge.difference_update(new_edge_labels)
        return False

    try:
        found = backtrack(0)
    except Budget:
        return {"found": False, "reason": f"budget exhausted after {budget_ms}ms — larger budget_ms may still find one"}
    if not found:
        return {"found": False, "reason": "search space exhausted — this tree has NO graceful labeling"}
    return {
        "found": True,
        "labels": dict(sorted(assignment.items())),
        "verdict": check_labeling(edges, assignment),
    }


def prufer_to_edges(seq, n):
    import heapq
    degree = [1] * n
    for x in seq:
        degree[x] += 1
    leaves = [i for i in range(n) if degree[i] == 1]
    heapq.heapify(leaves)
    edges = []
    for x in seq:
        leaf = heapq.heappop(leaves)
        edges.append([str(leaf), str(x)])
        degree[x] -= 1
        if degree[x] == 1:
            heapq.heappush(leaves, x)
    u = heapq.heappop(leaves)
    v = heapq.heappop(leaves)
    edges.append([str(u), str(v)])
    return edges


def random_trees(n, count, seed):
    if not (2 <= n <= MAX_N):
        return {"error": f"n must be between 2 and {MAX_N}"}
    if not (1 <= count <= MAX_COUNT):
        return {"error": f"count must be between 1 and {MAX_COUNT}"}
    rng = random.Random(seed)
    trees = []
    for _ in range(count):
        seq = [rng.randrange(n) for _ in range(n - 2)]
        trees.append({"edges": prufer_to_edges(seq, n)})
    return {"n": n, "count": count, "seed": seed, "trees": trees,
            "note": "uniform over LABELED trees (Prüfer); isomorphic shapes may repeat"}


def main() -> int:
    try:
        req = json.loads(sys.stdin.read())
        op = req["op"]
    except (KeyError, TypeError, ValueError) as e:
        print(json.dumps({"error": f"bad input: {e}. Expected JSON with an 'op' field."}))
        return 2

    try:
        if op == "find_labeling":
            out = find_labeling(req["edges"], int(req.get("budget_ms", DEFAULT_BUDGET_MS)))
        elif op == "random_trees":
            out = random_trees(int(req["n"]), int(req.get("count", 10)), int(req.get("seed", 0)))
        elif op == "check_labeling":
            edges, _ = parse_edges(req["edges"])
            labels = {str(k): int(v) for k, v in req["labels"].items()}
            out = check_labeling(edges, labels)
        else:
            out = {"error": f"unknown op {op!r}: use find_labeling, random_trees, or check_labeling"}
    except (KeyError, TypeError, ValueError) as e:
        out = {"error": f"bad arguments for op {op!r}: {e}"}

    print(json.dumps(out))
    return 0 if "error" not in out else 2


if __name__ == "__main__":
    sys.exit(main())
