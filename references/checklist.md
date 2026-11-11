# 上线验收：一票否决清单（含判定命令）

任意一条命中，**不许上线**。`<base>` 指站点根，例如 `https://example.com`。

先跑总检：

```
python3 scripts/seo_check.py audit <base>
```

退出码 0 = 可上线；非 0 = 有硬失败项，看输出里的 `FAIL` 行。下面每条给出手工复核命令，用于脚本判定有歧义时人工确认。

---

## 1. 内页的 h1 / 正文首段 / 内链不在 HTML 里（需要 JS 才有）

一条命令：`python3 scripts/seo_check.py render <base>/某个内页/`

手工：
```
curl -s -A "Googlebot" <base>/内页/ | grep -c '<a href'
curl -s -A "Googlebot" <base>/内页/ | grep -o '<h1' | wc -l
```

判别：h1 数量 = 1、内链 ≥ 2、正文词数 ≥ 300（游戏站/工具站阈值见 `游戏站开发与部署` §5.5）。

⚠️ **不要只 `grep 关键词` 判渲染**——关键词可能命中 meta description、内联 JSON-LD、或 `<title>`，会给出假通过。必须数 `<a href` 和 h1。

## 2. 直接访问二级及以上路径返回 404

```
python3 scripts/seo_check.py routes <base> --paths /about,/blog/,/blog/post-1/
```

判别：全部 200，且**不出现 308/301 链**（canonical 形态不该再跳）。SPA 的典型失败是只有首页 200、其余全 404。

## 3. 存在 noindex 或 robots 误屏蔽正式页

```
python3 scripts/seo_check.py robots <base>
```

手工：`curl -s <base>/robots.txt`，确认 `User-agent: *` 段落里没有 `Disallow: /`，也没有 `Disallow:` 挡住栏目目录。

页面级：`render` 输出里的 `noindex` 列必须是 `-`。

## 4. 孤儿页数量 > 0

```
python3 scripts/seo_check.py links <base> --max-pages 300
```

判别：`孤儿页` 列表为空。有孤儿页先加内链（首页 + 2 个相关页），不要用 sitemap 糊过去——sitemap 不算入链。

## 5. 两个页面抢同一个目标关键词

```
python3 - <<'PY'
import csv, collections
rows=list(csv.DictReader(open('keyword-map.csv', encoding='utf-8-sig')))
for col in ('目标词','slug'):
    dup={k:v for k,v in collections.Counter(r[col].strip().lower() for r in rows).items() if v>1}
    print(col, '重复:', dup or '无')
PY
```

判别：两列都输出「无」。有重复 = Z-2 违反，合并或改目标词，不许靠"反正内容不一样"解释。

## 6. 多个页面 title 或 h1 完全相同

```
python3 scripts/seo_check.py render <base>/a/ <base>/b/ <base>/c/ ... | grep -E 'title|h1'
```

或者跑 `links` 拿全站列表后批量 `render`。判别：title 两两不同、h1 两两不同。

程序化页面高发：变量缺失时整批退化成同一个 title。抽 10 个实例页比对，不要只看 2 个。

## 7. staging / 预览环境可被公开访问且未 noindex

手工验收（脚本测不了别的域名）：
1. 无痕窗口直接开 preview 域名 → 必须弹 Basic Auth，或页面 `<head>` 里就有 `noindex`。
2. `curl -sI https://preview-xxx.pages.dev | grep -i x-robots-tag` → 应有 `noindex`。
3. GSC 的「站点」列表里**不应该**有 preview 域名；有就是已被抓过，立刻处理。

## 8. sitemap 里包含 301 / 404 / noindex 地址

```
python3 scripts/seo_check.py sitemap <base>
```

判别：所有 loc 返回 200、canonical 与 loc 一致（忽略尾斜杠差异会单独提示）、`lastmod` 不等于当前时间。

⚠️ 尾斜杠差异会显示 `WARN canonical 形态不一致` 而不是 FAIL——但**上线前必须消除**，否则 sitemap 里一半地址会吃 308。

## 9. 旧 URL 没有一一对应的 301，或 301 全部指向首页

```
python3 scripts/seo_check.py routes <base> --map redirects.csv
```

`redirects.csv` 两列：`old,new`。判别：每条 old 返回 301、Location 的 host+path 与 new 一致、**不是 `/`**。指向首页的一律 FAIL。

## 10. 新站首批批量生成页面超过 20 个且未观察收录率

人工判定：查 `keyword-map.csv` 里 `状态=published` 的行数。超过 20 且站点上线不足 4 周 → 不许再放量，等 P-5 的收录率。

---

## 常规验收（非否决项，但会影响长期表现）

1. title 50~60 字符、description 140~160 字符——先 `html.unescape` 再算长度，`&amp;` 会多算 4 字符。
2. 每页 canonical 自指**且是绝对地址**（带 `https://域名`），`og:url` == canonical，`og:title` == `<title>`。
3. JSON-LD 里所有 `item` / `@id` / `url` 的尾斜杠形态与 canonical 一致（手写 JSON-LD 最常漏）。
4. 正文内链 ≥ 2 条**正文上下文锚**（不是导航/页脚/RelatedModules 样板块冒充的）——剥离方法见 `internal-link-audit.md` 第一节。
5. 首页能一步到达全部内容页（首页是权重最高、抓取最勤的页面）。
6. 移动端 LCP / INP / CLS 三条（C 章），首页 + 3 个主要模板页。
7. GA4 / 广告脚本 `async`，广告位有固定高度。
8. h2 之后直接 h3，不跳级；页尾 CTA 区**没有**第二个 `<h1>`（T-7）；列表项包在 `ul`/`ol` 的 `li` 里。
9. `<meta name="viewport" content="width=device-width, initial-scale=1">` 存在；点击目标 ≥48×48px；无横向滚动（C-5）。
10. 出站链接：付费/赞助带 `rel="sponsored"`，`target="_blank"` 带 `rel="noopener"`；**站内链接不加 rel**（I-12）。
11. 图片有 `srcset` / `<picture>` 出多尺寸，格式 WebP/AVIF；`loading="lazy"` 只出现在首屏以下的图（C-3）。
12. robots.txt 与页面级 `noindex` 没有互相矛盾的组合（U-12）。
13. 关键词密度（T-9 / T-9.1，house 必检项）：每页 frontmatter 有 `density_target`（默认 2–3% 短语加权密度）；实测并留数 target_keyword 出现次数、短语加权密度、频次密度、每千词频次；T-4 五个落点齐全；低于目标下限补足、超上限按堆砌处理。新增内页部署前先把这组数字发出来确认，密度以构建产物 / 线上实测为准。
14. **锚文本主词对齐（I-6.1 / I-6.2，house 必检项）**：从 sitemap 取全部页面，按 `internal-link-audit.md` 1.4 的取材方法剥离模板块、按目标 slug 分组，逐页人工核三项——① 至少 1 条入链锚文本含 `keyword-map.csv` 里的完整目标词；② 首页 / 最高权重栏目页那条含主词；③ 含主词 0 条、或全部是同一个纯精确词，都判不合格；入链 ≥4 条时至少保留 1 条描述型。**脚本只用来抓 HTML、分组、数条数，输出是取材底稿；合格结论人工逐条给，不拿退出码当结论。**
15. **补链位置（承接 `seo-inner-page-content` L2-2 ⑩）**：I-5 的回链不能全堆在页尾 Related guides，新页至少 1 条落在正文中部的自然语句里；人工读一遍正文确认，不要用页脚或导航块顶替。
