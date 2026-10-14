<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/banner/banner-dark.jpg">
  <img alt="tundra, Claude Code plugins" src="docs/banner/banner-light.jpg">
</picture>

[![Claude Code plugin marketplace](https://img.shields.io/badge/Claude%20Code-plugin%20marketplace-blue)](#install) [![License: Apache-2.0](https://img.shields.io/github/license/tundra-east/tundra)](LICENSE)

[English](README.md) | [繁體中文](README.zh-TW.md) | [简体中文](README.zh-CN.md)

</div>

A Claude Code plugin marketplace: plugins that take a ticket to a reviewed pull request, plugins that show you how you work, and one that fills in your timesheet.

<a id="install"></a>
## 📦 Install

```
/plugin marketplace add https://github.com/tundra-east/tundra.git
```

Then install the plugins you want from it:

```
/plugin install <plugin>@tundra
```

Run `/reload-plugins` afterwards to activate them.

Claude Code leaves auto-update off for a marketplace like this one, so you will not receive new versions until you turn it on: `/plugin` → **Marketplaces** → `tundra` → **Enable auto-update**.

Use the full HTTPS URL above. The `tundra-east/tundra` shorthand is also accepted, but it clones over SSH, which is a different instruction.

<a id="what-is-in-it"></a>
## 🗂️ What is in it

### 🔁 From ticket to pull request

Four plugins that take a ticket to a reviewed pull request, described in full in [their own README](docs/issue-to-pr/README.md). `issue-to-pr-pipeline` runs the whole flow, and on Claude Code v2.1.143 or later installing it installs the other three, each of which also works on its own.

<img alt="The resolve-issue-dashboard following a run: the runs by repository, the pipeline's eleven steps as a ring, the token figures and each agent's tool calls" src="docs/issue-to-pr/dashboard.png" width="560">

| Plugin | What it does |
|---|---|
| [`issue-to-pr-pipeline`](docs/issue-to-pr/README.md#issue-to-pr-pipeline) | Takes one ticket from diagnosis to a reviewed pull request, stopping for your approval of the plan and again before the PR opens |
| [`disconfirm-first`](docs/issue-to-pr/README.md#disconfirm-first) | Adversarial review of an issue, a plan, or an implemented fix, before the next step builds on it |
| [`test-authoring`](docs/issue-to-pr/README.md#test-authoring) | Finds test gaps and writes unit and integration tests, each checked by an independent verifier |
| [`pr-lifecycle`](docs/issue-to-pr/README.md#pr-lifecycle) | Opens a pull request in the style of your past ones and resolves its review comments, on Azure DevOps or GitHub |

Install all four at once:

```
/plugin install issue-to-pr-pipeline@tundra
```

### 🪞 How you work

#### [`whoami`](plugins/whoami/README.md)

A self-assessment from your code, your prompts, and your CLAUDE.md, as an HTML report. Only you can start it, with `/whoami:whoami`.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/whoami/thumbnails-dark.png">
  <img alt="Thumbnails of a whoami report: its header and summary, an axis with what you get right and what you miss, a diagram, and its tables" src="docs/whoami/thumbnails-light.png" width="360">
</picture>

Install it with:

```
/plugin install whoami@tundra
```

#### [`remind-me`](plugins/remind-me/README.md)

What you left open in the sessions you ran on a given day or over a range of days, checked against the live state of each pull request and branch, with a quick link back into each session.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/remind-me/thumbnails-dark.png">
  <img alt="Thumbnails of remind-me pages: a day's overview, a range with one card maximised, a repository over the range, and a session" src="docs/remind-me/thumbnails-light.png" width="360">
</picture>

Install it with:

```
/plugin install remind-me@tundra
```

### ⏱️ Your timesheet

#### [`tempo-logger`](plugins/tempo-logger/README.md)

Fills in your Tempo timesheet from what you say you did.

<img alt="A mock-up of Tempo's week view after a run: eight hours on the same work item on each of the five weekdays" src="docs/tempo-logger/week-filled.png" width="720">

Install it with:

```
/plugin install tempo-logger@tundra
```
