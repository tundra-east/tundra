<!-- translated from README.md, source sha256 84680f79507ea25e48d21df6ff7788b33cb2dfd477a00723ea58b24ac6141b9b; see the root CLAUDE.md "Translations" before editing -->
<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/banner/banner-dark.jpg">
  <img alt="tundra, Claude Code plugins" src="docs/banner/banner-light.jpg">
</picture>

[![CI](https://img.shields.io/github/actions/workflow/status/tundra-east/tundra/checks.yml?branch=main&label=CI)](https://github.com/tundra-east/tundra/actions/workflows/checks.yml) [![Claude Code plugin marketplace](https://img.shields.io/badge/Claude%20Code-plugin%20marketplace-blue)](#install) [![License: Apache-2.0](https://img.shields.io/github/license/tundra-east/tundra)](LICENSE)

[English](README.md) | [繁體中文](README.zh-TW.md) | [简体中文](README.zh-CN.md)

</div>

一個 Claude Code plugin marketplace：收錄把工單帶到經過審查的 pull request 的 plugin，讓你看清自己工作方式的 plugin，以及一個幫你填工時表的 plugin。

<a id="install"></a>
## 📦 安裝

```
/plugin marketplace add https://github.com/tundra-east/tundra.git
```

然後從中安裝你想要的 plugin：

```
/plugin install <plugin>@tundra
```

之後執行 `/reload-plugins` 來啟用它們。

像這樣的 marketplace，Claude Code 預設不開啟自動更新，所以在你開啟之前都不會收到新版本：`/plugin` → **Marketplaces** → `tundra` → **Enable auto-update**。

請使用上面完整的 HTTPS URL。`tundra-east/tundra` 這種簡寫也能用，但它透過 SSH clone，是不同的指令。

<a id="what-is-in-it"></a>
## 🗂️ 內容

### 🔁 從工單到 pull request

四個 plugin，把一張工單帶到經過審查的 pull request，完整說明在[它們自己的 README](docs/issue-to-pr/README.zh-TW.md)。`issue-to-pr-pipeline` 執行整個流程；在 Claude Code v2.1.143 或更新的版本上，安裝它就會一併安裝另外三個，而那三個也都能獨立使用。

<img alt="resolve-issue-dashboard 正在追蹤一次執行：依 repository 分組的執行清單、pipeline 的十一個步驟排成的環、token 統計，以及每個 agent 的工具呼叫" src="docs/issue-to-pr/dashboard.png" width="560">

| Plugin | 做什麼 |
|---|---|
| [`issue-to-pr-pipeline`](docs/issue-to-pr/README.zh-TW.md#issue-to-pr-pipeline) | 把一張工單從診斷帶到經過審查的 pull request，核准計畫前和開出 PR 前各停下來等你一次 |
| [`disconfirm-first`](docs/issue-to-pr/README.zh-TW.md#disconfirm-first) | 在下一步以它為基礎之前，對 issue、計畫或已實作的修正做對抗式審查 |
| [`test-authoring`](docs/issue-to-pr/README.zh-TW.md#test-authoring) | 找出測試缺口，撰寫單元測試與整合測試，每個測試都由獨立的驗證者檢查 |
| [`pr-lifecycle`](docs/issue-to-pr/README.zh-TW.md#pr-lifecycle) | 在 Azure DevOps 或 GitHub 上依你過去 PR 的風格開出 pull request，並處理它的審查意見 |

一次安裝全部四個：

```
/plugin install issue-to-pr-pipeline@tundra
```

### 🪞 你的工作方式

#### [`whoami`](plugins/whoami/README.zh-TW.md)

從你的程式碼、你的 prompt 和你的 CLAUDE.md 做一份自我評估，輸出成 HTML 報告。只有你能用 `/whoami:whoami` 啟動它。

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/whoami/thumbnails-dark.png">
  <img alt="whoami 報告的縮圖：標題與摘要、你做得好和常漏掉的一個面向、一張流程圖，以及表格" src="docs/whoami/thumbnails-light.png" width="360">
</picture>

安裝指令：

```
/plugin install whoami@tundra
```

#### [`remind-me`](plugins/remind-me/README.zh-TW.md)

你在某一天或一段日期裡跑過的 session 還留下哪些沒做完的事，逐一對照每個 pull request 和 branch 的即時狀態，並提供一鍵回到每個 session 的連結。

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/remind-me/thumbnails-dark.png">
  <img alt="remind-me 頁面的縮圖：單日概要、放大一張卡片的期間報告、一個 repository 在整段期間的頁面，以及一個 session" src="docs/remind-me/thumbnails-light.png" width="360">
</picture>

安裝指令：

```
/plugin install remind-me@tundra
```

### ⏱️ 你的工時表

#### [`tempo-logger`](plugins/tempo-logger/README.zh-TW.md)

依你說的工作內容填好 Tempo 工時表。

<img alt="執行後 Tempo 週檢視的示意圖：五個工作日每天都在同一個工作項目上記了八小時" src="docs/tempo-logger/week-filled.png" width="720">

安裝指令：

```
/plugin install tempo-logger@tundra
```
