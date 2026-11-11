#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seo_check.py — 网站 SEO 开发规范「可执行验收」（纯标准库，无第三方依赖）

配套 skill: seo-dev-spec。覆盖一票否决清单里可自动判定的 6 条：
  1 渲染语义在 HTML 里  2 二三级路径 200  3 robots 全局误挡 / 页面 noindex
  4 孤儿页与点击深度     6 title/h1 重复   8 sitemap 状态与 canonical

用法:
  python3 seo_check.py render https://x.com/a/ https://x.com/b/
  python3 seo_check.py routes https://x.com --paths /about,/blog/
  python3 seo_check.py routes https://x.com --map redirects.csv
  python3 seo_check.py sitemap https://x.com
  python3 seo_check.py robots https://x.com
  python3 seo_check.py links https://x.com --max-pages 200
  python3 seo_check.py audit https://x.com          # 一键总检，退出码 0 = 可上线

退出码: 0 全部通过 / 1 存在硬失败 / 2 参数或网络错误。
"""

import argparse
import concurrent.futures as futures
import csv
import gzip
import re
import ssl
import sys
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict, deque
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

GOOGLEBOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
LATIN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\u2019\-]*")

PASS, FAIL, WARN, INFO = "[OK]  ", "[FAIL]", "[WARN]", "[INFO]"


# ----------------------------------------------------------------------------- 终端输出

def dwidth(s):
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def pad(s, w):
    s = str(s)
    return s + " " * max(0, w - dwidth(s))


def table(headers, rows, indent="  "):
    cols = len(headers)
    data = [[pad(c, 0) for c in headers]] + [[pad(c, 0) for c in r] for r in rows]
    widths = [max(dwidth(row[i]) for row in data) for i in range(cols)]
    lines = []
    for idx, row in enumerate(data):
        line = indent + "  ".join(pad(c, widths[i]) for i, c in enumerate(row))
        lines.append(line.rstrip())
        if idx == 0:
            lines.append(indent + "  ".join("-" * widths[i] for i in range(cols)))
    print("\n".join(lines))


def head(title):
    print("")
    print("=" * 72)
    print(title)
    print("=" * 72)


# ----------------------------------------------------------------------------- 抓取

class _RedirectRecorder(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        self.chain = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.chain.append((code, newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, ua=GOOGLEBOT_UA, timeout=20, proxy=None, insecure=False, max_bytes=4_000_000):
    """返回 dict(status, final, chain, headers, body, error)。默认绕过系统代理。"""
    out = {"url": url, "status": None, "final": url, "chain": [], "headers": {}, "body": b"", "error": ""}
    rec = _RedirectRecorder()
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})]
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    handlers.append(urllib.request.HTTPSHandler(context=ctx))
    handlers.append(rec)
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        with opener.open(req, timeout=timeout) as resp:
            out["status"] = resp.status
            out["final"] = resp.geturl()
            out["headers"] = {k.lower(): v for k, v in resp.headers.items()}
            out["body"] = resp.read(max_bytes)
    except urllib.error.HTTPError as e:
        out["status"] = e.code
        out["final"] = url
        if e.headers:
            out["headers"] = {k.lower(): v for k, v in e.headers.items()}
        try:
            out["body"] = e.read(max_bytes)
        except Exception:
            pass
    except Exception as e:  # noqa: BLE001
        out["error"] = "%s: %s" % (type(e).__name__, e)
    out["chain"] = rec.chain
    return out


def decode_body(res):
    body = res.get("body") or b""
    enc = (res.get("headers", {}).get("content-encoding") or "").lower()
    if "gzip" in enc:
        try:
            body = gzip.decompress(body)
        except Exception:
            pass
    charset = "utf-8"
    m = re.search(r"charset=([\w\-]+)", res.get("headers", {}).get("content-type") or "", re.I)
    if m:
        charset = m.group(1)
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def run_parallel(fn, items, workers):
    items = list(items)
    if not items:
        return []
    if workers <= 1 or len(items) == 1:
        return [fn(i) for i in items]
    with futures.ThreadPoolExecutor(max_workers=min(workers, len(items))) as ex:
        return list(ex.map(fn, items))


# ----------------------------------------------------------------------------- 解析

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.h1 = []
        self.meta_robots = ""
        self.canonical = ""
        self.links = []          # [[href, anchor_text], ...]
        self.text_parts = []
        self._skip = 0
        self._in_title = False
        self._h1_buf = None
        self._a_idx = None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in SKIP_TAGS:
            self._skip += 1
            return
        if tag == "title":
            self._in_title = True
        elif tag == "h1":
            self._h1_buf = []
        elif tag == "a":
            href = a.get("href", "").strip()
            if href:
                self.links.append([href, ""])
                self._a_idx = len(self.links) - 1
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name == "robots":
                self.meta_robots += " " + a.get("content", "")
            elif name == "googlebot":
                self.meta_robots += " " + a.get("content", "")
        elif tag == "link":
            if "canonical" in (a.get("rel") or "").lower():
                self.canonical = a.get("href", "").strip()

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if tag == "title":
            self._in_title = False
        elif tag == "h1" and self._h1_buf is not None:
            self.h1.append(" ".join("".join(self._h1_buf).split()))
            self._h1_buf = None
        elif tag == "a":
            self._a_idx = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._skip:
            return
        self.text_parts.append(data)
        if self._h1_buf is not None:
            self._h1_buf.append(data)
        if self._a_idx is not None:
            self.links[self._a_idx][1] += data

    @property
    def text(self):
        return " ".join(self.text_parts)

    @property
    def noindex(self):
        r = self.meta_robots.lower()
        return "noindex" in r or "none" in r


def word_count(text):
    """中英混排词数：CJK 按字计 + 拉丁按词计（与全页口径一致，不同口径不要混比）。"""
    cjk = len(CJK_RE.findall(text))
    rest = CJK_RE.sub(" ", text)
    return cjk + len(LATIN_RE.findall(rest))


def parse_html(html):
    p = PageParser()
    try:
        p.feed(html)
    except Exception:
        pass
    return p


# ----------------------------------------------------------------------------- URL 工具

def norm(url, drop_query=False, strip_slash=False):
    s = urlsplit(url)
    path = s.path or "/"
    if strip_slash and len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit((s.scheme.lower(), s.netloc.lower(), path, "" if drop_query else s.query, ""))


def host_key(url):
    h = urlsplit(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def is_internal(base, href):
    href = (href or "").strip()
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return False
    target = urljoin(base, href)
    s = urlsplit(target)
    if s.scheme not in ("http", "https"):
        return False
    return host_key(target) == host_key(base)


def abs_url(base, href):
    return norm(urljoin(base, href), drop_query=True)


# ----------------------------------------------------------------------------- render

def inspect(url, args, ua=None):
    r = fetch(url, ua=ua or args.ua, timeout=args.timeout, proxy=args.proxy, insecure=args.insecure)
    info = {
        "url": url, "status": r["status"], "error": r["error"], "chain": r["chain"],
        "title": "", "h1n": 0, "h1": "", "words": 0, "links": 0, "canonical": "",
        "noindex": False, "final": r["final"],
    }
    if r["error"]:
        return info
    p = parse_html(decode_body(r))
    info["title"] = " ".join(p.title.split())
    info["h1n"] = len(p.h1)
    info["h1"] = p.h1[0] if p.h1 else ""
    info["words"] = word_count(p.text)
    info["links"] = len([h for h, _ in p.links if is_internal(url, h)])
    info["canonical"] = p.canonical
    info["noindex"] = p.noindex
    return info


def cmd_render(args):
    head("渲染检查 (Z-1 / R-1 / T-2) — 需要用 JS 才有内容的页面在这里会露出来")
    rows, failures = [], 0
    infos = run_parallel(lambda u: inspect(u, args), args.urls, args.workers)
    for i in infos:
        url = i["url"]
        if i["error"]:
            rows.append([url, "ERR", "-", "-", "-", "-", i["error"][:40]])
            failures += 1
            continue
        flags = []
        if not (200 <= (i["status"] or 0) < 300):
            flags.append("status")
        if i["h1n"] != 1:
            flags.append("h1!=1")
        if i["words"] < args.min_words:
            flags.append("words<%d" % args.min_words)
        if i["links"] < args.min_links:
            flags.append("links<%d" % args.min_links)
        if i["noindex"]:
            flags.append("noindex")
        if flags:
            failures += 1
        rows.append([
            url[:56], i["status"], i["h1n"], i["words"], i["links"],
            "yes" if i["noindex"] else "-",
            "FAIL " + ",".join(flags) if flags else "OK",
        ])
    table(["URL", "码", "h1", "词数", "内链", "noindex", "判定"], rows)

    titles = Counter(i["title"] for i in infos if i["title"] and not i["error"])
    print("")
    print("  判定口径: h1 必须为 1；正文词数 >= %d；内链 >= %d；noindex 必须为 no。" % (args.min_words, args.min_links))
    print("  ⚠️ 只 grep 关键词不算数——关键词会命中 meta / JSON-LD / title，产生假通过。")
    if titles:
        dup = {k: v for k, v in titles.items() if v > 1}
        if dup:
            print("  %s title 重复: %s" % (FAIL, list(dup.keys())[:3]))
            failures += 1
    return 1 if failures else 0


# ----------------------------------------------------------------------------- routes

def load_map(path):
    pairs = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames and {"old", "new"} <= {f.strip().lower() for f in reader.fieldnames}:
            for row in reader:
                row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                if row.get("old"):
                    pairs.append((row["old"], row.get("new", "")))
        else:
            f.seek(0)
            for row in csv.reader(f):
                if len(row) >= 2 and row[0].strip():
                    pairs.append((row[0].strip(), row[1].strip()))
    return pairs


def cmd_routes(args):
    base = args.base.rstrip("/")
    targets = []
    if args.map:
        pairs = load_map(args.map)
        targets = [(o if o.startswith("http") else urljoin(base + "/", o.lstrip("/")),
                    (n if n.startswith("http") else urljoin(base + "/", n.lstrip("/"))) if n else "")
                   for o, n in pairs]
    elif args.paths:
        for p in args.paths.replace("\n", ",").split(","):
            p = p.strip()
            if p:
                targets.append((p if p.startswith("http") else urljoin(base + "/", p.lstrip("/")), ""))
    else:
        print("需要 --paths 或 --map", file=sys.stderr)
        return 2

    head("路由检查 (R-3 / U-4 / U-5 / M-3) — 直接访问二三级路径 + 301 映射")
    rows, failures = [], 0
    results = run_parallel(lambda t: fetch(t[0], ua=args.ua, timeout=args.timeout,
                                           proxy=args.proxy, insecure=args.insecure), targets, args.workers)
    for (want, expect), r in zip(targets, results):
        status = r["status"] if r["status"] is not None else "ERR"
        loc = ""
        chain_desc = ""
        if r["chain"]:
            chain_desc = str(r["chain"][0][0])
            loc = r["chain"][0][1]
        elif r["status"] in (301, 302, 307, 308):
            loc = r["headers"].get("location", "")
        # 首跳状态码：urllib 会跟随跳转，r["status"] 是最终码，判 301 必须看首跳
        hop = r["chain"][0][0] if r["chain"] else r["status"]

        flags = []
        if expect:
            # 映射模式：首跳必须 301、落在 expect 上、不指向首页、目标自身 200
            if hop != 301:
                flags.append("非301(%s)" % hop)
            if loc:
                got = norm(urljoin(want, loc))
                if norm(expect, strip_slash=True) != norm(got, strip_slash=True):
                    flags.append("目标不符")
                if urlsplit(got).path in ("", "/"):
                    flags.append("指向首页")
            if not loc:
                flags.append("无Location")
            if r["status"] != 200:
                flags.append("目标非200(%s)" % status)
        else:
            # 普通路径模式：必须直接 200，且不应再跳
            if r["status"] != 200:
                flags.append("非200(%s)" % status)
            if r["chain"]:
                flags.append("发生跳转")
        if flags:
            failures += 1
        rows.append([want[:52], status, chain_desc or "-", (loc or "-")[:46],
                     ("FAIL " + ",".join(flags)) if flags else "OK"])
    table(["URL", "最终码", "跳转", "Location", "判定"], rows)
    print("")
    print("  判定口径: 普通路径必须直接 200；映射必须 301 且 Location 等于目标、不得指向 /。")
    return 1 if failures else 0


# ----------------------------------------------------------------------------- sitemap

def parse_sitemap(text):
    urls, children = [], []
    try:
        root = ET.fromstring(text.encode("utf-8", errors="replace"))
    except Exception as e:  # noqa: BLE001
        return urls, children, "XML 解析失败: %s" % e
    for child in root:
        tag = child.tag.split("}")[-1]
        if tag == "url":
            loc, lm = "", ""
            for sub in child:
                st = sub.tag.split("}")[-1]
                if st == "loc":
                    loc = (sub.text or "").strip()
                elif st == "lastmod":
                    lm = (sub.text or "").strip()
            if loc:
                urls.append((loc, lm))
        elif tag == "sitemap":
            for sub in child:
                if sub.tag.split("}")[-1] == "loc" and sub.text:
                    children.append(sub.text.strip())
    return urls, children, ""


def cmd_sitemap(args):
    base = args.base.rstrip("/")
    sm_url = args.sitemap_url or (base + "/sitemap.xml")
    head("sitemap 检查 (U-8 / U-9 / U-10) — %s" % sm_url)

    r = fetch(sm_url, ua=args.ua, timeout=args.timeout, proxy=args.proxy, insecure=args.insecure)
    if r["error"] or (r["status"] or 0) >= 400:
        print("  %s 无法读取 sitemap: status=%s %s" % (FAIL, r["status"], r["error"]))
        return 1

    urls, children, err = parse_sitemap(decode_body(r))
    if err:
        print("  %s %s" % (FAIL, err))
        return 1
    failures = 0
    if children:
        print("  %s 这是 sitemap index，展开 %d 个子 sitemap（最多取 20 个）" % (INFO, len(children)))
        for child_url in children[:20]:
            cr = fetch(child_url, ua=args.ua, timeout=args.timeout, proxy=args.proxy, insecure=args.insecure)
            if cr["error"] or (cr["status"] or 0) >= 400:
                print("  %s 子 sitemap 读不到: %s" % (FAIL, child_url))
                failures += 1
                continue
            u2, _, e2 = parse_sitemap(decode_body(cr))
            if e2:
                print("  %s 子 sitemap 解析失败: %s" % (FAIL, child_url))
                failures += 1
            urls.extend(u2)

    total = len(urls)
    print("  条目总数: %d" % total)
    if total == 0:
        print("  %s sitemap 里没有 URL" % FAIL)
        return 1
    if total > 50000:
        print("  %s 超过 5 万条，必须拆 sitemap index (U-9)" % FAIL)
        failures += 1

    sample = urls[: args.limit]
    if total > args.limit:
        print("  %s 只抽查前 %d 条（--limit 可调）" % (INFO, args.limit))

    def check(entry):
        loc, lm = entry
        rr = fetch(loc, ua=args.ua, timeout=args.timeout, proxy=args.proxy, insecure=args.insecure)
        canon = ""
        if rr["status"] and 200 <= rr["status"] < 300 and (rr["headers"].get("content-type") or "").find("html") >= 0:
            canon = parse_html(decode_body(rr)).canonical
        return loc, lm, rr, canon

    results = run_parallel(check, sample, args.workers)
    today = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
    rows = []
    bad_status = bad_canon = bad_lastmod = 0
    for loc, lm, rr, canon in results:
        flags = []
        if rr["error"]:
            flags.append("取不到")
        elif rr["status"] != 200:
            flags.append("状态%s" % rr["status"])
        if canon and norm(canon, strip_slash=True) != norm(loc, strip_slash=True):
            if norm(canon) == norm(loc):
                flags.append("canonical尾斜杠不一致")
            else:
                flags.append("canonical指向别处")
        if lm and lm.startswith(today):
            flags.append("lastmod=今天(疑似动态)")
        if "状态" in "".join(flags) or "取不到" in "".join(flags):
            bad_status += 1
        if "canonical" in "".join(flags):
            bad_canon += 1
        if "lastmod" in "".join(flags):
            bad_lastmod += 1
        if flags:
            rows.append([loc[:62], lm[:10] or "-", ",".join(flags)])

    if rows:
        table(["URL", "lastmod", "问题"], rows[:40])
        if len(rows) > 40:
            print("  ... 另有 %d 条问题未显示" % (len(rows) - 40))
    else:
        print("  %s 抽查 %d 条全部 200 且 canonical 自指" % (PASS, len(sample)))
    print("")
    print("  统计: 状态异常 %d / canonical 异常 %d / lastmod 疑似动态 %d" % (bad_status, bad_canon, bad_lastmod))
    if bad_status or bad_canon:
        failures += 1
    return 1 if failures else 0


# ----------------------------------------------------------------------------- robots

def cmd_robots(args):
    base = args.base.rstrip("/")
    head("robots.txt 检查 (U-11 / D-1) — %s/robots.txt" % base)
    r = fetch(base + "/robots.txt", ua=args.ua, timeout=args.timeout, proxy=args.proxy, insecure=args.insecure)
    if r["error"] or r["status"] != 200:
        print("  %s robots.txt 取不到 (status=%s)；没有 robots.txt 是允许的，但要确认不是 5xx" % (WARN, r["status"]))
        return 0
    text = decode_body(r)
    print("  ---- robots.txt ----")
    for line in text.splitlines()[:40]:
        print("  | " + line)
    print("  --------------------")

    failures = 0
    blocks, cur = defaultdict(list), None
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip().lower(), v.strip()
        if k == "user-agent":
            cur = v.lower()
            blocks.setdefault(cur, [])
        elif k == "disallow" and cur:
            blocks[cur].append(v)

    sitemap_declared = "sitemap:" in text.lower()
    for agent in ("*", "googlebot"):
        for path in blocks.get(agent, []):
            if path == "/":
                print("  %s User-agent: %s 有 `Disallow: /` —— 全站被挡，立刻修" % (FAIL, agent))
                failures += 1
            elif path and not any(x in path for x in ("/admin", "/cart", "/checkout", "/search", "/tmp", "/private", "/preview")):
                print("  %s User-agent: %s 屏蔽了 `%s` —— 确认这是有意为之（正式内容页不许挡）" % (WARN, agent, path))
    if "user-agent: *" not in text.lower():
        print("  %s 没有 `User-agent: *` 段落" % WARN)
    print("  %s Sitemap 声明: %s" % (PASS if sitemap_declared else WARN, "有" if sitemap_declared else "无（建议加一行 Sitemap: ...）"))
    print("")
    print("  判定口径: 正式内容页不许被 Disallow。站内搜索结果 / 筛选页应该允许抓取 + noindex，而不是屏蔽（被挡的页读不到 noindex）。")
    return 1 if failures else 0


# ----------------------------------------------------------------------------- links

def crawl(base, args):
    start = base.rstrip("/") + "/"
    depth = {start: 0}
    scheduled = {start}
    q = deque([start])
    pages = {}
    inlinks = defaultdict(set)
    depth_limit = max(args.max_depth, 1) + 1

    while q and len(pages) < args.max_pages:
        batch = []
        while q and len(batch) < args.workers and len(pages) + len(batch) < args.max_pages:
            u = q.popleft()
            if u not in pages:
                batch.append(u)
        if not batch:
            break
        results = run_parallel(
            lambda u: fetch(u, ua=args.ua, timeout=args.timeout, proxy=args.proxy, insecure=args.insecure),
            batch, args.workers)
        for u, r in zip(batch, results):
            info = {"status": r["status"], "error": r["error"], "title": "", "h1": "", "h1n": 0,
                    "words": 0, "noindex": False, "links": []}
            if not r["error"] and r["status"] and 200 <= r["status"] < 300:
                p = parse_html(decode_body(r))
                info["title"] = " ".join(p.title.split())
                info["h1n"] = len(p.h1)
                info["h1"] = p.h1[0] if p.h1 else ""
                info["words"] = word_count(p.text)
                info["noindex"] = p.noindex
                info["links"] = [abs_url(u, h) for h, _ in p.links if is_internal(u, h)]
            pages[u] = info
            for t in set(info["links"]):
                if t.rstrip("/") == u.rstrip("/"):
                    continue
                inlinks[t].add(u)
                if t not in scheduled and depth[u] + 1 <= depth_limit:
                    scheduled.add(t)
                    depth[t] = depth[u] + 1
                    q.append(t)
    return pages, inlinks, depth, scheduled


def sitemap_urls(base, args, max_children=20):
    """取 sitemap（含 index 子项）里的全部 loc，用于孤儿页对照。"""
    r = fetch(base + "/sitemap.xml", ua=args.ua, timeout=args.timeout, proxy=args.proxy, insecure=args.insecure)
    if r["error"] or r["status"] != 200:
        return []
    urls, children, _ = parse_sitemap(decode_body(r))
    locs = [u for u, _ in urls]
    for cu in children[:max_children]:
        cr = fetch(cu, ua=args.ua, timeout=args.timeout, proxy=args.proxy, insecure=args.insecure)
        if not cr["error"] and cr["status"] == 200:
            locs.extend(u for u, _ in parse_sitemap(decode_body(cr))[0])
    return [norm(u, drop_query=True) for u in locs]


def find_orphans(base, pages, inlinks, scheduled, expected):
    """孤儿页 = 从首页完全不可达（只在 sitemap 里） ∪ 可达但 0 入链。

    只统计 pages 里的页面会漏掉最严重的一类：从首页根本走不到的页面
    压根不会进爬取结果，必须拿 sitemap 做全集对照。
    """
    reach = {u.rstrip("/") for u in pages} | {u.rstrip("/") for u in scheduled}
    unreachable = [u for u in expected if u.rstrip("/") not in reach]
    zero_in = [u for u, v in pages.items()
               if u.rstrip("/") != base and v["status"] and 200 <= v["status"] < 300
               and not (inlinks.get(u, set()) - {u})]
    seen, out = set(), []
    for u in sorted(unreachable) + sorted(zero_in):
        k = u.rstrip("/")
        if k in seen:
            continue
        seen.add(k)
        out.append(u)
    return out, unreachable


def cmd_links(args):
    base = args.base.rstrip("/")
    head("内链检查 (I-1 / I-2) — 从首页 BFS 爬 %d 页" % args.max_pages)
    pages, inlinks, depth, scheduled = crawl(base, args)
    ok = {u: v for u, v in pages.items() if v["status"] and 200 <= v["status"] < 300}
    print("  抓到 200 页面: %d / 已调度: %d" % (len(ok), len(scheduled)))

    # 入链排行
    top = sorted(ok.keys(), key=lambda u: -len(inlinks.get(u, ())))[:12]
    table(["入链最多的页面", "入链数"], [[u[:60], len(inlinks.get(u, ()))] for u in top])

    expected = [] if args.no_sitemap else sitemap_urls(base, args)
    if not args.no_sitemap and not expected:
        print("")
        print("  %s 拿不到 sitemap，孤儿页只能按「可达但 0 入链」判，会漏掉完全不可达的页" % WARN)
    orphans, unreachable = find_orphans(base, pages, inlinks, scheduled, expected)

    if orphans:
        print("")
        print("  %s 孤儿页 %d 个（sitemap 不算入链）" % (FAIL, len(orphans)))
        print("     其中从首页完全不可达: %d 个（最严重，必须加内链）" % len(unreachable))
        for u in orphans[:20]:
            mark = "不可达" if u.rstrip("/") in {x.rstrip("/") for x in unreachable} else "0入链"
            print("    - [%s] %s" % (mark, u))
    else:
        print("")
        print("  %s 孤儿页: 0" % PASS)

    # 点击深度
    deep = sorted([(d, u) for u, d in depth.items() if d > args.max_depth])
    if deep:
        print("")
        print("  %s 点击深度 > %d 的页面 %d 个" % (WARN, args.max_depth, len(deep)))
        for d, u in deep[:15]:
            print("    - depth=%d  %s" % (d, u))
    else:
        print("  %s 所有页面点击深度 <= %d" % (PASS, args.max_depth))

    # 出链异常
    zero = [u for u, v in ok.items() if not v["links"]]
    huge = [(u, len(v["links"])) for u, v in ok.items() if len(v["links"]) > 100]
    if zero:
        print("  %s 出链为 0 的页面 %d 个: %s" % (WARN, len(zero), ", ".join(x[:40] for x in zero[:5])))
    if huge:
        print("  %s 出链 > 100 的页面 %d 个: %s" % (WARN, len(huge), ", ".join("%s(%d)" % (u[:34], n) for u, n in huge[:5])))

    print("")
    print("  ⚠️ 本表统计的是全部 <a href>（含 Nav / RelatedModules / 页脚模板块）。")
    print("     要证明「正文有上下文锚」，必须按 references/internal-link-audit.md 第 1.3 节剥离样板块人工核。")
    return 1 if orphans else 0


# ----------------------------------------------------------------------------- audit

def cmd_audit(args):
    base = args.base.rstrip("/")
    head("一键总检 — %s" % base)
    verdict = []

    rc_robots = cmd_robots(args)
    verdict.append(("3 robots 无误挡", rc_robots == 0))

    rc_sitemap = cmd_sitemap(args)
    verdict.append(("8 sitemap 无 301/404/noindex 条目", rc_sitemap == 0))

    pages, inlinks, depth, scheduled = crawl(base, args)
    ok = {u: v for u, v in pages.items() if v["status"] and 200 <= v["status"] < 300}
    expected = sitemap_urls(base, args)
    orphans, unreachable = find_orphans(base, pages, inlinks, scheduled, expected)
    head("渲染与重复度抽样 (Z-1 / T-2 / T-6)")
    sample = sorted(ok.keys())[: args.sample]
    rows, bad = [], 0
    for u in sample:
        v = ok[u]
        flags = []
        if v["h1n"] != 1:
            flags.append("h1=%d" % v["h1n"])
        if v["words"] < args.min_words:
            flags.append("词数%d" % v["words"])
        if v["noindex"]:
            flags.append("noindex")
        if flags:
            bad += 1
        rows.append([u[:52], v["h1n"], v["words"], len(v["links"]),
                     "FAIL " + ",".join(flags) if flags else "OK"])
    table(["URL", "h1", "词数", "内链", "判定"], rows)

    tdup = {k: n for k, n in Counter(v["title"] for v in ok.values() if v["title"]).items() if n > 1}
    hdup = {k: n for k, n in Counter(v["h1"] for v in ok.values() if v["h1"]).items() if n > 1}

    verdict.append(("1 渲染（h1/词数/内链/noindex）", bad == 0))
    verdict.append(("4 孤儿页为 0", len(orphans) == 0))
    verdict.append(("6 title / h1 无重复", not tdup and not hdup))
    if tdup:
        print("  %s title 重复: %s" % (FAIL, list(tdup.items())[:3]))
    if hdup:
        print("  %s h1 重复: %s" % (FAIL, list(hdup.items())[:3]))
    if orphans:
        print("  %s 孤儿页 %d 个: %s" % (FAIL, len(orphans), ", ".join(u[:40] for u in orphans[:5])))

    # 路由抽样：取 sitemap 里最深层的 5 条
    deep_paths = sorted({urlsplit(u).path for u in ok if urlsplit(u).path.count("/") >= 2})[:5]
    if deep_paths:
        head("二三级路径抽查 (R-3)")
        res = run_parallel(lambda p: fetch(base + p, ua=args.ua, timeout=args.timeout,
                                           proxy=args.proxy, insecure=args.insecure), deep_paths, args.workers)
        bad_routes = 0
        rows = []
        for p, rr in zip(deep_paths, res):
            flags = []
            if rr["status"] != 200:
                flags.append("非200(%s)" % rr["status"])
            if rr["chain"]:
                flags.append("有跳转")
            if flags:
                bad_routes += 1
            rows.append([p[:56], rr["status"], "FAIL " + ",".join(flags) if flags else "OK"])
        table(["路径", "码", "判定"], rows)
        verdict.append(("2 二三级路径 200", bad_routes == 0))

    head("结论")
    failed = [name for name, okv in verdict if not okv]
    for name, okv in verdict:
        print("  %s %s" % (PASS if okv else FAIL, name))
    print("")
    print("  未自动覆盖、必须人工确认的: 5 目标词重复(keyword-map) / 7 staging 可访问 / 9 旧 URL 301 映射 / 10 放量数量")
    print("  映射检查请另跑: python3 %s routes %s --map redirects.csv" % (sys.argv[0].split("/")[-1], base))
    if failed:
        print("")
        print("  结论: 不可上线（%d 项未通过）" % len(failed))
        return 1
    print("")
    print("  结论: 可上线（人工项仍需逐条确认）")
    return 0


# ----------------------------------------------------------------------------- CLI

def build_parser():
    p = argparse.ArgumentParser(
        prog="seo_check.py",
        description="seo-dev-spec 配套检查脚本（纯标准库）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n  seo_check.py audit https://example.com\n  seo_check.py routes https://example.com --map redirects.csv\n")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--ua", default=GOOGLEBOT_UA, help="User-Agent（默认 Googlebot）")
        sp.add_argument("--timeout", type=int, default=20)
        sp.add_argument("--proxy", default=None, help="走代理，如 http://127.0.0.1:7890；默认绕过系统代理")
        sp.add_argument("--insecure", action="store_true", help="跳过证书校验（仅排障用）")
        sp.add_argument("--workers", type=int, default=8)

    r = sub.add_parser("render", help="抽查页面 HTML 里有没有 h1 / 正文 / 内链")
    r.add_argument("urls", nargs="+")
    r.add_argument("--min-words", type=int, default=300)
    r.add_argument("--min-links", type=int, default=2)
    common(r)
    r.set_defaults(func=cmd_render)

    ro = sub.add_parser("routes", help="状态码 / 跳转链 / 301 映射校验")
    ro.add_argument("base")
    ro.add_argument("--paths", default="", help="逗号分隔的路径，如 /about,/blog/")
    ro.add_argument("--map", default="", help="redirects.csv（old,new）")
    common(ro)
    ro.set_defaults(func=cmd_routes)

    s = sub.add_parser("sitemap", help="sitemap 条目状态、canonical、lastmod")
    s.add_argument("base")
    s.add_argument("--sitemap-url", default="")
    s.add_argument("--limit", type=int, default=150)
    common(s)
    s.set_defaults(func=cmd_sitemap)

    rb = sub.add_parser("robots", help="robots.txt 全局误挡检查")
    rb.add_argument("base")
    common(rb)
    rb.set_defaults(func=cmd_robots)

    l = sub.add_parser("links", help="孤儿页 / 点击深度 / 出链异常")
    l.add_argument("base")
    l.add_argument("--max-pages", type=int, default=200)
    l.add_argument("--max-depth", type=int, default=3)
    l.add_argument("--no-sitemap", action="store_true", help="不拿 sitemap 对照（默认会对照，能抓出从首页完全不可达的页）")
    common(l)
    l.set_defaults(func=cmd_links)

    a = sub.add_parser("audit", help="一键总检（退出码 0 = 可上线）")
    a.add_argument("base")
    a.add_argument("--max-pages", type=int, default=200)
    a.add_argument("--max-depth", type=int, default=3)
    a.add_argument("--limit", type=int, default=80)
    a.add_argument("--sample", type=int, default=10)
    a.add_argument("--min-words", type=int, default=300)
    a.add_argument("--sitemap-url", default="")
    common(a)
    a.set_defaults(func=cmd_audit)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n中断", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
