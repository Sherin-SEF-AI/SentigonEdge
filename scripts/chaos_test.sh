#!/usr/bin/env bash
# Chaos test: kill a Kafka consumer mid-batch, restart it, prove zero event loss.
set -u
cd "$(dirname "$0")/.."
N=${1:-100}

echo "1. produce $N numbered messages"
uv run python -m bench.chaos_durability produce "$N" 2>&1 | tail -1

echo "2. start consumer, kill it mid-batch (SIGKILL after 3s)"
uv run python -m bench.chaos_durability consume > /tmp/chaos_c1.log 2>&1 &
sleep 3
pkill -9 -f "bench.chaos_durability consume" 2>/dev/null
sleep 1
before=$(sort -un /tmp/chaos_processed.log 2>/dev/null | grep -c .)
echo "   killed mid-batch; unique processed before kill: $before / $N"

echo "3. restart consumer, fixed 18s drain window (covers group rebalance + remainder)"
uv run python -m bench.chaos_durability consume > /tmp/chaos_c2.log 2>&1 &
sleep 18
pkill -9 -f "bench.chaos_durability consume" 2>/dev/null
sleep 1

echo "4. RESULT"
python3 - "$N" <<'PY'
import sys
n=int(sys.argv[1])
ids=[int(x) for x in open("/tmp/chaos_processed.log")] if __import__("os").path.exists("/tmp/chaos_processed.log") else []
uniq=set(ids)
missing=sorted(set(range(n))-uniq)
print(f"   unique ids processed:          {len(uniq)} / {n}")
print(f"   total processings (w/ replay): {len(ids)}  (replayed duplicates = {len(ids)-len(uniq)})")
print(f"   missing ids (event loss):      {missing if missing else 'NONE'}")
print("   ==> ZERO EVENT LOSS: every message processed at-least-once across the kill" if not missing
      else "   ==> LOSS DETECTED")
PY
