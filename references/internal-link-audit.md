# 内链审计与改版迁移作业单

## 一、内链审计（每次改版必做，常规每月一次）

一条命令先出底稿：

```
python3 scripts/seo_check.py links <base> --max-pages 300
```

输出四张表：入链数排行、孤儿页清单、点击深度 > 3 的页面、出链异常页（出链 > 100 或 = 0）。

### 1.1 孤儿页必须清零（I-1）

判据：`孤儿页` 列表为空。修法只有一种——**加内链**，从首页或最热栏目页 + 2 个内容相关页各加一条。不要用 sitemap 顶替，sitemap 不算入链。

### 1.2 点击深度 ≤ 3（I-2）

深度 > 3 的页面，两种修法：塞到同层路径，或加一个中间聚合页（栏目页）承接。不要靠"页脚全站链接"解决——那是降权信号。

### 1.3 ⚠️ 关键动作：剥离站点级模板块再统计

**这是内链审计最容易自欺的一步。** `Nav` / `RelatedModules` / 页脚这些组件渲染在 `<main>` 内，所以"只数 `<main>` 里的链接"**筛不掉它们**——结果是样板块的链接冒充成正文上下文锚，让人误判"早链好了"。

手工核法：
1. 抽 `<main>` 的 HTML。
2. 按 `Related guides` / `相关推荐` 这类块标题**切断**，只统计切断点之前的正文部分。
3. 判定"某目标词有没有被正文承接"，以切断点之前的锚为准。

脚本输出的 `links` 表统计的是**全部** `<a href>`（含模板），所以它适合查孤儿页和深度，**不适合**证明"正文有上下文锚"。后者必须人工按上面三步核一遍。

### 1.4 锚文本分布与主词对齐（I-6 / I-6.1 / I-6.2）

**先按 1.3 剥离模板块，再按目标 slug 分组。** `links` 输出的条数里混着导航与页脚，直接拿它看锚文本会得出「每条都链了」的假象。

取材脚本（标准库，只出底稿；**合格与否人工逐条判，不拿脚本输出当结论**）：

```python
# 用法：python3 anchor_audit.py https://域名 /path/to/keyword-map.csv
import csv, html, re, sys, urllib.request
from collections import defaultdict
from html.parser import HTMLParser

base, mapfile = sys.argv[1], sys.argv[2]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(url):
    return opener.open(urllib.request.Request(url, headers={'User-Agent': 'Googlebot'}), timeout=25).read().decode('utf-8', 'replace')

class Links(HTMLParser):
    def __init__(self): super().__init__(); self.out=[]; self.cur=None; self.buf=[]
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            h = dict(attrs).get('href','')
            if h.startswith('/') and not h.startswith('//'): self.cur=h.split('#')[0].split('?')[0]; self.buf=[]
    def handle_data(self, d):
        if self.cur is not None: self.buf.append(d)
    def handle_endtag(self, tag):
        if tag == 'a' and self.cur is not None:
            t = re.sub(r'\s+',' ',html.unescape(''.join(self.buf))).strip()
            if t: self.out.append((self.cur,t))
            self.cur=None

paths = [re.sub(r'^https?://[^/]+', '', u) or '/' for u in
         re.findall(r'<loc>([^<]+)', get(base.rstrip('/')+'/sitemap.xml'))]
pages = {}
for path in paths:                       # 只取 path：sitemap 的 loc 常是生产绝对地址，
    p = Links(); p.feed(get(base.rstrip('/') + path))   # 本地预览时不取 path 会抓到线上旧版本
    pages[path.rstrip('/') or '/'] = p.out
occ = defaultdict(int)
for links in pages.values():
    for pair in set(links): occ[pair] += 1
template = {pair for pair,n in occ.items() if n >= max(3,int(len(pages)*0.7))}  # 导航/页脚
inbound = defaultdict(list)
for src, links in pages.items():
    for href, text in links:
        if (href,text) not in template: inbound[href].append((src,text))

kw = {}
for row in csv.reader(open(mapfile, encoding='utf-8-sig')):
    if row and row[0].strip().startswith('/'): kw[row[0].strip()] = row[1].strip()
for slug, word in sorted(kw.items()):
    rows = inbound.get(slug, [])
    exact = [(s,t) for s,t in rows if word.lower() in t.lower()]
    print(f'--- {slug} [{word}] inbound={len(rows)} exact_phrase={len(exact)}')
    for s,t in dict.fromkeys(rows):
        print(f'    {"EXACT" if word.lower() in t.lower() else "descr":6s} {s:34s} {t[:78]}')
```

人工判四件事（I-6 / I-6.1 / I-6.2）：
1. **主词 0 条 = 不合格**。全写成功能描述句（「Work out how long your script takes to say」）看起来合规，实际词面为 0，首页那条权重最高的链接没传主词 → 把首页那条改成含完整目标词。
2. **首页 / 最高权重栏目页那条必须含完整目标词**；目标词以 `keyword-map.csv` 为准，不拿 title 的近似写法代替。
3. **单一锚文本字符串占比 > 60%**（I-6）→ 改一部分为长尾变体 / 描述型；入链 ≥4 条时至少保留 1 条描述型，不要为了传主词把整批都改成精确词。
4. **全部文案一模一样（复制粘贴）→ 检查 href 是否也指对了。实测踩过：文案与 href 不符，最大词族的权重传错了目标。** 卡片型链接（整块包在 `<a>` 内）改描述文案就等于改锚文本，不用动结构。

⚠️ **不要把 I-6 的「≤60%」读成「精确词要尽量少用」**：那个百分比是同一条锚文本字符串的重复率，不是含主词锚文本的占比。2026-11-09 全站 18 页审计里，5 个老页含主词 0 条就是这个误读造成的。

改完锚文本后复核两件：**I-8 承诺兑现**（含主词的锚文本点进去，title / h1 语义上说的是同一件事）、**T-9 密度**（锚文本也是正文可见词，改完重新实测三项口径并留数）。

### 1.5 出链异常

出链 = 0：页面是死胡同，加相关推荐。
出链 > 100：多半是页脚堆砌或列表页无节制展开，压到合理范围；列表页可以只展示前 N 条 + 分页。

---

## 二、改版 301 映射作业流程（U-4 / 第 9 章）

### 2.1 导出旧 URL 全集（三源合并，缺一会漏）

1. **GSC** → Indexing → Pages → Export，拿「已编入索引」和「已抓取未索引」两批。
2. **旧 sitemap.xml**（含 sitemap index 下的子 sitemap，全部展开）。
3. **服务器 / CF 访问日志**：取近 90 天有真实访问或外链来源的路径。

合并去重后写入 `old_urls.txt`。**只用 sitemap 会漏掉早期被外链、后来被从 sitemap 摘掉的历史页**——这些恰恰是最需要 301 的。

### 2.2 生成映射

写 `redirects.csv`，两列 `old,new`：

1. 每条 old 指向**语义最接近**的新页。不是"最像的栏目"，是"用户真正想看的那个页面"。
2. 实在没有对应页 → **410**，不要 301 到首页。全站 301 到首页 = 软 404 + 权重稀释，等于放弃这些页。
3. 一对多的情况（一个旧页拆成两个新页）选主承接页，其余内容在新页做交叉链接。
4. 大量同类前缀可以用规则压缩（`/old-dir/*` → `/new-dir/:splat`），但**压缩后必须逐条验样本**，不能只验规则。

### 2.3 上线前验证（一条不合就不许发）

```
python3 scripts/seo_check.py routes <base> --map redirects.csv
```

判别：
- 每条 old 状态码 **301**（不是 302、不是 308、不是 200）
- Location 的 host + path 与 new 一致（query string 视情况保留，凡带参的旧 URL 要单独测一条）
- **没有一条指向 `/`**
- 每条 new 自身返回 **200**（301 到 404 是最常见的上线事故）

### 2.4 上线后 30 天监控

每周查 GSC：Indexing → Pages 里的 404、软 404、重定向错误；Performance 里旧 URL 的曝光是否平滑迁移到新 URL。

1. 旧 URL 仍在出曝光 → 说明 Google 还没换掉，301 正常工作的表现，别去动。
2. 旧 URL 出 404 → 映射漏了，补 301。
3. 新 URL 曝光起不来 → 检查内链是否也改到了新址（301 只传权，不传内链锚文本）。

### 2.5 保留周期

有外链的旧 URL 的 301 **永久保留**。其余至少 12 个月。删 301 等于主动断掉历史权重。

---

## 三、换域名作业单（M-5）

1. 新域先能正常访问，全站 https + 证书正常。
2. 全站改：canonical、og:url、sitemap 的 loc、内链、JSON-LD 里的 url、hreflang（如有）。**一处不改就是两套地址。**
3. 旧域全站 301 到新域**同路径**（保留 path + query）。
4. GSC 里**新旧两个属性都要保留**，新属性验证通过、提交新 sitemap，旧属性留着看迁移进度（GSC 有"地址更改"工具，可提交）。
5. 旧域 DNS 不要下线，301 至少要跑满 12 个月。
6. ⚠️ 两个域名不要同时提供完整站点的 200 响应——重复内容会让权重分裂，比不换还差。
7. 换域名后 2 周内不要同时做大规模内容改版，出问题无法归因。
