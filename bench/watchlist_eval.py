"""Watchlist / ReID embedding discriminability eval (real data, no labels needed).

ByteTrack gives us free identity structure: a contiguous run of appearances for one
track id (no timestamp gap > GAP seconds) is one real person sighting. We split each
sighting in half (reference vs held-out probe = a same-identity pair) and pair each
reference against other sightings' probes (cross-identity pairs). The gap between the
same-identity and cross-identity cosine distributions is exactly the embedder's
identity discriminability, which is what an appearance watchlist stands on.

Run:  uv run python bench/watchlist_eval.py [camera_id]
"""

from __future__ import annotations

import math
import random
import statistics
import sys
from datetime import datetime

from qdrant_client import QdrantClient
from qdrant_client import models as qm

GAP = 8.0  # seconds; larger gap => new sighting => new identity instance
MIN_APP = 6  # a sighting needs this many appearances to split into ref+probe


def cos(a: list[float], b: list[float]) -> float:
    d = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return d / (na * nb) if na and nb else 0.0


def mean(vs: list[list[float]]) -> list[float]:
    n = len(vs)
    return [sum(v[i] for v in vs) / n for i in range(len(vs[0]))]


def main() -> None:
    c = QdrantClient(url="http://localhost:6335")
    must = [qm.FieldCondition(key="object_class", match=qm.MatchValue(value="person"))]
    if len(sys.argv) > 1:
        must.append(qm.FieldCondition(key="camera_id", match=qm.MatchValue(value=sys.argv[1])))
    flt = qm.Filter(must=must)

    rows: dict[tuple, list[tuple[datetime, list[float]]]] = {}
    off = None
    seen = 0
    while seen < 20000:
        pts, off = c.scroll(
            "reid", scroll_filter=flt, limit=512, offset=off,
            with_payload=True, with_vectors=True,
        )
        for p in pts:
            pl = p.payload or {}
            cam, t, ts = pl.get("camera_id"), pl.get("track_id"), pl.get("ts")
            if p.vector is not None and ts is not None:
                rows.setdefault((cam, t), []).append((datetime.fromisoformat(ts), list(p.vector)))
        seen += len(pts)
        if off is None or not pts:
            break

    sightings: list[list[list[float]]] = []
    for items in rows.values():
        items.sort(key=lambda x: x[0])
        cur = [items[0][1]]
        last = items[0][0]
        for ts, v in items[1:]:
            if (ts - last).total_seconds() > GAP:
                if len(cur) >= MIN_APP:
                    sightings.append(cur)
                cur = []
            cur.append(v)
            last = ts
        if len(cur) >= MIN_APP:
            sightings.append(cur)

    if len(sightings) < 3:
        print(f"only {len(sightings)} sightings; let the index populate more, then rerun")
        return

    refs, probes, same = [], [], []
    for s in sightings:
        h = len(s) // 2
        r, p = mean(s[:h]), mean(s[h:])
        refs.append(r)
        probes.append(p)
        same.append(cos(r, p))

    random.seed(0)
    idx = list(range(len(sightings)))
    cross = [
        cos(refs[i], probes[j])
        for i in idx
        for j in random.sample([x for x in idx if x != i], min(20, len(idx) - 1))
    ]

    ss = sorted(same)
    cs = sorted(cross)
    print(f"person sightings: {len(sightings)}   same-pairs: {len(same)}   cross-pairs: {len(cross)}")
    print(f"same-identity  : mean {statistics.mean(same):.3f}  p10 {ss[len(ss)//10]:.3f}  min {min(same):.3f}")
    print(f"cross-identity : mean {statistics.mean(cross):.3f}  p90 {cs[int(len(cs)*0.9)]:.3f}  p99 {cs[min(len(cs)-1,int(len(cs)*0.99))]:.3f}  max {max(cross):.3f}")
    print("\nthreshold | recall(same>=t) | false-match(cross>=t)")
    best = None
    for t in [x / 100 for x in range(60, 97)]:
        rec = sum(1 for s in same if s >= t) / len(same)
        fm = sum(1 for s in cross if s >= t) / len(cross)
        if t in (0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.88, 0.90, 0.92, 0.95):
            print(f"  {t:.2f}    |     {rec:5.1%}      |    {fm:5.1%}")
        # operating point: lowest false-match while keeping recall >= 0.8
        if rec >= 0.80 and (best is None or fm < best[2]):
            best = (t, rec, fm)
    if best:
        print(f"\nsuggested threshold (recall>=80%): {best[0]:.2f} -> recall {best[1]:.1%}, false-match {best[2]:.1%}")


if __name__ == "__main__":
    main()
