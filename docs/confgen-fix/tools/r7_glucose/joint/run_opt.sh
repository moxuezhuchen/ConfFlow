#!/bin/bash
# R7-joint ORCA XTB2 无约束优化运行器（归档版）。
# 原机器绝对路径已参数化，集中在本文件头。用法：
#   ./run_opt.sh [输入列表] [输出目录]
# 输入列表由 r7_joint_sample.py 生成（默认 ./inputs_joint100.list）。
set -u
LIST="${1:-./inputs_joint100.list}"
OUT="${2:-./opt_joint100}"
ORCA_BIN="${ORCA_BIN:-/opt/orca611/orca}"
mkdir -p "$OUT"
run_one(){
  INP="$1"
  BASE=$(basename "$INP" .inp)
  D="$OUT/$BASE"
  mkdir -p "$D"
  cp "$INP" "$D/$BASE.inp"
  START=$(date +%s.%N)
  "$ORCA_BIN" "$D/$BASE.inp" > "$D/$BASE.out" 2>&1
  RC=$?
  END=$(date +%s.%N)
  WALL=$(python3 -c "print(float('$END')-float('$START'))")
  echo "$BASE rc=$RC wall_s=$WALL" >> "$OUT/progress.log"
  echo "$BASE rc=$RC wall_s=$WALL"
  return $RC
}
export -f run_one
export OUT ORCA_BIN
rm -f "$OUT/progress.log"
cat "$LIST" | xargs -P 4 -I{} bash -c 'run_one "$@"' _ {}
echo "ALL DONE rc=$?"
wc -l "$OUT/progress.log"; cat "$OUT/progress.log"
