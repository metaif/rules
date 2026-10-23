#!/usr/bin/env bash
# 把最新的 SSRDog + TagInternet 订阅节点离线合并进 OpenWrt 上正在运行的 OpenClash 配置，
# 校验通过后热重载。可重复执行（幂等）。
#
#   ROUTER=192.168.100.100 ./openclash-sync.sh            # 合并并生效
#   ROUTER=192.168.100.100 ./openclash-sync.sh --dry-run  # 只生成并对拍校验，不落地
#
# 依赖：本机 python3 / scp / ssh（免密登录路由器）；路由器上有 /etc/openclash/clash。
set -euo pipefail

ROUTER="${ROUTER:-root@192.168.100.100}"
CLASH_DIR=/etc/openclash
RUN_CFG="$CLASH_DIR/SSRDog-TagInternet-Merged.yaml"
SUBS=("$CLASH_DIR/config/SSRDog.yaml" "$CLASH_DIR/config/TagInternet.yaml")
HERE="$(cd "$(dirname "$0")" && pwd)"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

SSH=(ssh -o BatchMode=yes -o ConnectTimeout=10 "$ROUTER")
SCP=(scp -q -o BatchMode=yes -o ConnectTimeout=10)

W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
echo "== 1/5 拉取最新订阅与当前运行配置 =="
"${SCP[@]}" "$ROUTER:$RUN_CFG" "$W/scaffold.yaml"
"${SCP[@]}" "$ROUTER:${SUBS[0]}" "$W/ssrdog.yaml"
"${SCP[@]}" "$ROUTER:${SUBS[1]}" "$W/tag.yaml"
ls -l "$W" | awk 'NR>1{printf "   %s\n", $NF}'

echo "== 2/5 离线重建节点集合 =="
python3 "$HERE/openclash-merge.py" "$W/scaffold.yaml" "$W/ssrdog.yaml" "$W/tag.yaml" "$W/merged.yaml"

echo "== 3/5 语法校验 =="
"${SCP[@]}" "$W/merged.yaml" "$ROUTER:/tmp/openclash-merged.yaml"
"${SSH[@]}" "SAFE_PATHS=$CLASH_DIR:/usr/share/openclash $CLASH_DIR/clash -t -d $CLASH_DIR -f /tmp/openclash-merged.yaml 2>&1 | grep -iE 'level=error|successful|failed'"

if [ "$DRY" = 1 ]; then
  echo "== --dry-run：未落地，生成物在 $W/merged.yaml（临时目录将删除，需保留请另行导出） =="
  exit 0
fi

echo "== 4/5 备份并安装 =="
STAMP="$(date +%Y%m%d-%H%M%S)"
"${SSH[@]}" "cp -p $RUN_CFG $RUN_CFG.bak-codex-$STAMP && cp /tmp/openclash-merged.yaml $RUN_CFG && chmod 600 $RUN_CFG && ls -l $RUN_CFG | awk '{print \"   installed \", \$5, \"bytes\"}'"

echo "== 5/5 热重载并确认 =="
"${SSH[@]}" 'bash -s' <<EOF
C=$RUN_CFG
SEC=\$(grep -m1 -E '^secret:' "\$C" | sed -E 's/^secret:[[:space:]]*//; s/["'"'"']//g')
api(){ curl -s --max-time 25 -H "Authorization: Bearer \$SEC" "\$@"; }
echo "   reload HTTP=\$(api -o /dev/null -w '%{http_code}' -X PUT -H 'Content-Type: application/json' -d "{\"path\":\"\$C\"}" 'http://127.0.0.1:9090/configs?force=true')"
sleep 3
echo "   节点总数: \$(api http://127.0.0.1:9090/proxies | tr ',' '\n' | grep -c '"type":"AnyTLS"\|"type":"Trojan"\|"type":"Vless"\|"type":"Shadowsocks"\|"type":"Hysteria"')"
for g in 流媒体 Google服务 Broker AI 家宽; do
  printf '   %-10s -> ' "\$g"; api "http://127.0.0.1:9090/proxies/\$g" | tr ',' '\n' | grep -m1 '"now"'
done
EOF
echo "完成。回滚: cp $RUN_CFG.bak-codex-$STAMP $RUN_CFG 然后热重载。"
