#!/usr/bin/env python3
"""离线重建 OpenClash 合并配置的「节点集合」。

背景：路由器上的 SSRDog-TagInternet-Merged.yaml 是「SSRDog + TagInternet + 私有节点」
的合并产物，正常情况下由外部订阅转换器生成；本脚本改为在本地离线重建，避免依赖外部转换 API。

做什么：
  1. 保留 scaffold（当前运行配置）里的所有骨架 —— dns / rules / tun / sniffer / 各分组定义；
  2. 用最新的 SSRDog、TagInternet 订阅重建 proxies 列表（+ scaffold 里的私有节点，名字以 [ 开头）；
  3. 按与 full.ini 相同的正则，重建 国家组 与 家宽组 的成员；
  4. 重新固定 流媒体 / Google服务 / Broker 的默认出口（家宽一律降到末尾）。

用法：
  openclash-merge.py <scaffold.yaml> <ssrdog.yaml> <tag.yaml> <out.yaml>
"""
import re
import sys

NAME = re.compile(r'^\s*-\s*\{?\s*name:\s*(?:"([^"]*)"|\'([^\']*)\'|([^,}]+))')
EMOJI_ESC = re.compile(r'\\U([0-9A-Fa-f]{8})')
EMOJI = re.compile(r'[\U0001F000-\U0001FAFF☀-➿]')

# 分组默认出口（首个成员 = 默认）。AI 组按用户要求不动。
FRONT = {
    '流媒体': ['新加坡'],
    'Google服务': ['新加坡'],
    'Broker': ['香港', 'DIRECT'],
}
# 与 Clash/full.ini 中的 custom_proxy_group 正则保持一致
MATCH = {
    '美国': r'(美国|美國|US|USA|United States|America|洛杉矶|洛杉磯|圣何塞|聖何塞|西雅图|西雅圖|纽约|紐約|达拉斯|達拉斯|硅谷)',
    '日本': r'(日本|东京|東京|大阪|JP|Japan)',
    '台湾': r'(台湾|台灣|台北|新北|彰化|TW|Taiwan)',
    '香港': r'(香港|港|HK|Hong Kong|HongKong|hongkong)',
    '新加坡': r'(新加坡|狮城|獅城|SG|Singapore)',
    '英国': r'(英国|英國|UK|GB|United Kingdom|Britain|London|伦敦|倫敦)',
    '法国': r'(法国|法國|FR|France|Paris|巴黎)',
    '德国': r'(德国|德國|DE|Germany|Frankfurt|法兰克福|法蘭克福|Berlin|柏林)',
    '土耳其': r'(土耳其|TR|Turkey|Türkiye|Turkiye|Istanbul|伊斯坦布尔|伊斯坦堡)',
    '家宽': r'(家宽|家寬|住宅|Residential|residential|ISP)',
}
SSR_SKIP = ('GB |', 'Reset', 'Expire')          # 订阅里的信息节点，不是真节点


def load(path):
    return open(path, encoding='utf-8', errors='replace').read().split('\n')


def name_of(line):
    m = NAME.match(line.rstrip())
    if not m:
        return None
    g = next((x for x in m.groups() if x is not None), None)
    return g.strip() if g is not None else None


def section(L, key, nxt):
    s = next(i for i, l in enumerate(L) if l.strip() == key)
    e = next(i for i, l in enumerate(L) if l.startswith(nxt) and i > s)
    return s, e, L[s+1:e]


def items(lines):
    """按列表项切分；只在「首个层级」的 '- ' 处切，避免误切嵌套列表。"""
    out, cur, base = [], None, None
    for l in lines:
        m = re.match(r'^(\s*)- ', l)
        if m and (base is None or len(m.group(1)) == base):
            if base is None:
                base = len(m.group(1))
            if cur:
                out.append(cur)
            cur = [l]
        elif cur is not None:
            cur.append(l)
    if cur:
        out.append(cur)
    return out


def decode(s):
    """\\U0001F1ED -> 真 emoji，仅用于正则匹配。"""
    return EMOJI_ESC.sub(lambda m: chr(int(m.group(1), 16)), s or '')


def norm(s):
    return re.sub(r'\s+', ' ', re.sub(r'[^\x20-\x7e一-鿿]', '', decode(s))).strip()


def strip_flag(line):
    """去掉 SSRDog 节点名里的 emoji，保持与本机既有命名一致。"""
    m = re.search(r"""name:\s*(['"])(.*?)\1""", line)
    if not m:
        return line
    cleaned = EMOJI.sub('', m.group(2)).strip()
    return line[:m.start()] + "name: '%s'" % cleaned + line[m.end():]


def main(scaffold, ssrdog, tag, out):
    cur = load(scaffold)
    cs, ce, cprox = section(cur, 'proxies:', 'proxy-groups:')
    gs, ge, _ = section(cur, 'proxy-groups:', 'rules:')
    rs = next(i for i, l in enumerate(cur) if l.rstrip() == 'rules:')

    _, _, ssrprox = section(load(ssrdog), 'proxies:', 'proxy-groups:')
    _, _, tagprox = section(load(tag), 'proxies:', 'proxy-groups:')

    ssr_items = [it for it in items(ssrprox)
                 if not any(k in (name_of(it[0]) or '') for k in SSR_SKIP)]
    ssr_items = [[strip_flag(it[0]).lstrip()] + [l.lstrip() for l in it[1:]] for it in ssr_items]
    tag_items = items(tagprox)

    cur_items = items(cprox)
    priv_items = [it for it in cur_items if (name_of(it[0]) or '').startswith('[')]
    old_names = [name_of(it[0]) for it in cur_items]

    new_items = ssr_items + tag_items + priv_items
    new_names = [name_of(it[0]) for it in new_items]
    q = lambda n: '  - "%s"' % n

    def rebuild_members(it, members):
        pi = next(i for i, l in enumerate(it) if l.strip().startswith('proxies:'))
        return it[:pi + 1] + [q(n) for n in members]

    out_groups = []
    for it in items(cur[gs+1:ge]):
        gname = name_of(it[0])
        if gname == '手动':                       # 持有 &1 锚点 = 全量节点，自动组复用
            it = rebuild_members(it, new_names)
        elif gname in MATCH:
            pat = re.compile(MATCH[gname])
            it = rebuild_members(it, [n for n in new_names if pat.search(norm(n))])
        elif gname in FRONT:
            members = [l[4:].strip().strip('"') for l in it if l.startswith('  - ')]
            spec = [x for x in FRONT[gname] if x in members]
            rest = [x for x in members if x not in spec and x != '家宽']
            tail = ['家宽'] if '家宽' in members else []
            it = rebuild_members(it, spec + rest + tail)
        out_groups.append(it)

    lines = (cur[:cs+1]
             + [l for it in new_items for l in it]
             + cur[ce:gs+1]
             + [l for it in out_groups for l in it]
             + cur[ge:])
    open(out, 'w', encoding='utf-8').write('\n'.join(lines))

    added = [n for n in new_names if norm(n) not in set(map(norm, old_names))]
    removed = [n for n in old_names if norm(n) not in set(map(norm, new_names))]
    print(f"节点 {len(cur_items)} -> {len(new_items)}"
          f"  (SSRDog {len(ssr_items)} + TagInternet {len(tag_items)} + 私有 {len(priv_items)})")
    print(f"  新增 {len(added)}: {[decode(x) for x in added][:6]}{' ...' if len(added) > 6 else ''}")
    print(f"  下线移除 {len(removed)}")
    for g in ('美国', '日本', '香港', '台湾', '新加坡', '家宽'):
        pat = re.compile(MATCH[g])
        print(f"  组[{g}] = {sum(1 for n in new_names if pat.search(norm(n)))}")
    print(f"  写出 {out}")


if __name__ == '__main__':
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    main(*sys.argv[1:5])
