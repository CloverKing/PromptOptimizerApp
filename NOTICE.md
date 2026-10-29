# NOTICE

## 本项目

**PromptOptimizerApp** — Prompt Optimizer 的 Windows 单文件桌面封装
Copyright (C) 2026 CloverKing
https://github.com/CloverKing/PromptOptimizerApp

## 上游项目

**prompt-optimizer** — An AI prompt optimizer for writing better prompts and getting better AI results.
Copyright (C) 2025 linshenkx
https://github.com/linshenkx/prompt-optimizer
Licensed under the GNU Affero General Public License v3.0 only (AGPL-3.0-only).

## 修改说明（AGPL-3.0 第 5 条要求的显著修改声明）

本项目是上述上游项目的衍生作品，修改方式为：

1. `upstream/` 目录以 `git subtree` 方式原样引入上游源码（含其完整 Git 提交来源信息），
   **未对其中任何文件做修改**；
2. 新增 `desktop/` 目录，使用 Python + WebView2 提供桌面窗口、本地静态服务与系统集成，
   替代上游自带的 Electron 打包方式；
3. 新增 `scripts/`、`.github/` 等构建与发布配套文件；
4. 前端界面的功能、文案、图标均来自上游项目，未做改动。

## 源码获取

依据 AGPL-3.0 第 13 条，本程序的完整对应源码随仓库公开发布：
https://github.com/CloverKing/PromptOptimizerApp

发布的 `PromptOptimizerApp.exe` 单文件产物即由该仓库的 `main` 分支（对应 tag 见 Release 说明）
通过 `python desktop/build.py` 构建得到。
