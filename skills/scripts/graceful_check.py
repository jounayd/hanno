#!/usr/bin/env python3
"""Graceful-labeling checker (stdlib only, no eval — safe to expose).

stdin: JSON {"edges": [[u, v], ...], "labels": {"0": 0, "1": 3, ...}}
       Vertices may be any string/int keys; labels must be integers.
stdout: JSON verdict explaining exactly which property holds or fails:
  - is_tree: connected and |E| == |V| - 1
  - labels_valid: injective, exactly the set {0..n-1}
  - edge_labels: the |u-v| values, and whether they are exactly {1..n-1}
  - graceful: the final verdict
"""

import json
import sys


def main() -> int:
    try:
        data = json.loads(sys.stdin.read())
        edges = [(str(u), str(v)) for u, v in data["edges"]]
        labels = {str(k): int(v) for k, v in data["labels"].items()}
    except (KeyError, TypeError, ValueError) as e:
        print(
            json.dumps(
                {
                    "error": f"bad input: {e}. Expected {{'edges': [[u,v],...], 'labels': {{vertex: int}}}}"
                }
            )
        )
        return 2

    vertices = set(labels.keys())
    for u, v in edges:
        vertices.add(u)
        vertices.add(v)
    n = len(vertices)

    missing = sorted(v for v in vertices if v not in labels)
    if missing:
        print(json.dumps({"error": f"vertices with no label: {missing}"}))
        return 2

    # connectivity + edge count => tree
    adjacency = {v: [] for v in vertices}
    for u, v in edges:
        adjacency[u].append(v)
        adjacency[v].append(u)
    seen = set()
    stack = [next(iter(vertices))] if vertices else []
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(adjacency[cur])
    is_tree = (len(edges) == n - 1) and (len(seen) == n)

    label_values = sorted(labels.values())
    labels_valid = label_values == list(range(n))

    edge_label_list = sorted(abs(labels[u] - labels[v]) for u, v in edges)
    edge_labels_valid = edge_label_list == list(range(1, n))

    verdict = {
        "n_vertices": n,
        "n_edges": len(edges),
        "is_tree": is_tree,
        "labels_valid": labels_valid,
        "label_values": label_values,
        "edge_labels": edge_label_list,
        "edge_labels_valid": edge_labels_valid,
        "graceful": bool(is_tree and labels_valid and edge_labels_valid),
    }
    print(json.dumps(verdict))
    return 0


if __name__ == "__main__":
    sys.exit(main())
