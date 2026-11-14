# seo-dev-spec

A numbered SEO development and acceptance specification for websites, packaged as a WorkBuddy Skill. It answers exactly three questions: what must not be done, how to judge it, and how to run acceptance.

This repository holds the specification, its reference notes, a keyword-map template and a dependency-free checker. `SKILL.md` — the rule source — is kept in its original Chinese; this README is the English guide to the package.

## Background

SEO advice is contradictory by nature. In day-to-day site work the same question gets different answers from framework defaults, old tutorials, role prompts and half-remembered blog posts: FID versus INP, whether a content site may be a SPA, whether Vercel's bare-domain 308 needs an explicit 301, whether every image should be lazy, whether keyword density deserves a number at all. When the answer changes with whoever is answering, a checklist is not enough — you need a single source of truth that can be cited by number.

This spec grew out of that problem. Every rule carries an identifier, conflicts are resolved by an explicit appendix instead of by preference, and anything mechanically checkable gets a command. The design choice is deliberate: a rule that cannot be cited and cannot be tested will be re-litigated on every project.

Two revisions are worth knowing, both dated 2026-11-09. Keyword density was moved from "vanity metric" to a house-required check that must be measured on three definitions and recorded in a per-page `density_target` (v1.3). Internal links then gained two-way anchor-text checks, so a site cannot pass while sending zero exact main keywords to its target pages (v1.4).

## What it does

1. Sets three non-negotiable baselines: main content and internal links must be present in the returned HTML; one target keyword per page; no bulk page generation for a new site.
2. Organises the rest into nine numbered families — rendering and crawlability (R), URL and routing (U), internal linking (I), titles and TDK (T), keyword map (K), programmatic-SEO gating (P), performance (C), deployment (D) and 301 migration (M) — on top of the Z baselines.
3. Ships ten launch veto items, each with a copyable judgement command.
4. Provides an indexing-troubleshooting decision tree covering four failure modes: not indexed, crawled but not indexed, indexed without ranking, and a site-wide drop.
5. Adds an internal-link audit procedure, on-page copywriting notes, and a rule-source index that also records which sources were deliberately not adopted and why.
6. Provides `scripts/seo_check.py`, a Python 3 standard-library checker that automates six of the veto items.

## When to use it

Read it before building or changing any website, page template, URL, redirect or release, and run the checker at the acceptance gate. Cite its rule numbers when another document, expert prompt or older tutorial disagrees on an SEO decision.

It does not define the order of a launch, platform-specific deployment pitfalls, or keyword and data collection; those belong to separate skills. It explicitly does not replace front-end engineering quality or a full accessibility checklist (keyboard, contrast, form labels, ARIA) — those remain the implementer's responsibility.

## Repository contents

- `SKILL.md` — the numbered rule source (Chinese), including the conflict-resolution appendix.
- `references/checklist.md` — the ten veto items with copyable judgement commands.
- `references/internal-link-audit.md` — link audit procedure, anchor-text keyword alignment, and a 301 mapping workflow for redesigns.
- `references/index-troubleshooting.md` — the indexing decision tree.
- `references/onpage-copywriting.md` — keyword families, anchor-text types, product-page block order, and an explicit note that single-page samples are not norms.
- `references/sources.md` — sources per rule, plus rejected sources and the reasons for rejecting them.
- `scripts/seo_check.py` — dependency-free checker.
- `assets/keyword-map.csv` — the one-page-one-keyword template that acts as the single source of truth for routes and TDK.

## How to use

1. Read `SKILL.md` before writing or changing pages. Treat its rule numbers as the authority on SEO decisions.
2. Fill `assets/keyword-map.csv` for your site: one row per URL, one target keyword per row, plus the per-page `density_target`.
3. Build pages so that h1, body copy, breadcrumbs and internal links appear in the server-returned HTML as real `<a href>` elements.
4. Run the acceptance check before release:

```bash
python3 scripts/seo_check.py audit https://your-domain.com
```

5. Use an individual subcommand when you need a narrower answer:

```bash
python3 scripts/seo_check.py render https://your-domain.com/page-a/ https://your-domain.com/page-b/
python3 scripts/seo_check.py routes https://your-domain.com --paths /about,/blog/
python3 scripts/seo_check.py routes https://your-domain.com --map redirects.csv
python3 scripts/seo_check.py sitemap https://your-domain.com
python3 scripts/seo_check.py robots https://your-domain.com
python3 scripts/seo_check.py links https://your-domain.com --max-pages 200
```

Exit codes are `0` (all pass), `1` (hard failure) and `2` (argument or network error).

Use the checker as a data-collection tool, not as the final verdict. A passing exit code does not replace reading the HTML and counting words, keyword occurrences, h1/h2 structure and internal links on real pages.

## Requirements

- Python 3, standard library only. No packages, credentials, network accounts or private services are bundled.
- The checker fetches public URLs over HTTP(S), so the machine running it needs outbound network access.

## Scope and limitations

- `SKILL.md` and the reference notes are in Chinese. This README is the English entry point; it is a guide, not a translation of the rule source.
- The spec names other skills (`站点上线总纲`, `游戏站开发与部署`, `gefei-seo`, `gsc-query`, `geo-ai-traffic`). They are separate packages and are not included here.
- Rule revisions are dated; treat the version and dates in `SKILL.md` as the record of what changed.

## Origin

Extracted from a personal WorkBuddy skill library and cleaned for public release: machine-specific absolute paths were replaced with portable ones, backup and build artifacts were excluded, and no credentials are included.

## Companion site

A first-person account of why this specification exists, what it checks and who should read it is published at [WorkBuddy Central: SEO Dev Spec](https://workbuddycentral.com/seo-dev-spec/). This repository remains the rule source; that page records the reasoning, not a second copy of the rules.

WorkBuddy Central is an independent, unofficial documentation site. It is not affiliated with WorkBuddy or Tencent.
