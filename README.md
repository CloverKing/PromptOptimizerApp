# PromptOptimizerApp

把 [Prompt Optimizer](https://github.com/linshenkx/prompt-optimizer)（一个纯前端的 AI 提示词优化 Web 应用）封装成 **Windows 单文件 exe**，并且能持续、无冲突地跟随上游更新。

> 一句话选型：上游自带的 Electron 桌面端只能产出 NSIS 安装包 / zip，本项目改用
> **Python + WebView2（pywebview）** 承载同一套前端产物，得到一个免安装、约 15 MB 的单文件程序，
> 同时**不修改上游任何一行代码**——这是后续能无痛同步上游的前提。

---

## 1. 项目规划

```
PromptOptimizerApp/
├── upstream/                       # ① 源项目：git subtree 引入，v2.13.0，只读
│   ├── packages/core               #    业务内核
│   ├── packages/ui                 #    组件库
│   └── packages/web                #    Vite 前端入口（桌面端直接消费它的 dist）
├── desktop/                        # ② 新桌面端（本仓库自己的代码）
│   ├── app/
│   │   ├── main.py                 #    启动流程：单实例 → 服务 → 窗口 → 注入
│   │   ├── server.py               #    127.0.0.1 静态服务（固定 origin）
│   │   ├── config.py               #    config.json 读写与校验
│   │   ├── paths.py                #    源码/单文件两种形态的路径解析
│   │   ├── jsapi.py                #    暴露给前端的 window.pywebview.api
│   │   ├── single_instance.py      #    命名互斥量 + 拉回已有窗口
│   │   ├── updater.py              #    GitHub Release 版本检查
│   │   ├── inject.js               #    页面加载后注入的桌面脚本
│   │   └── version.py              #    封装层版本号
│   ├── assets/                     #    图标（沿用上游视觉）
│   ├── build.py                    #    一键构建：编译前端 → 拷贝产物 → 打包 exe
│   ├── PromptOptimizerApp.spec      #    PyInstaller 配置
│   └── requirements.txt
├── scripts/sync-upstream.ps1|.sh   # ③ 同步上游
└── .github/workflows/              # ④ CI：打 tag 自动出 Release
```

三个目录的职责边界很硬：

| 目录 | 谁维护 | 能不能改 |
| --- | --- | --- |
| `upstream/` | 上游作者 | **不能改**。改了下次 `git subtree pull` 就冲突 |
| `desktop/` | 本项目 | 随便改，与上游零重叠 |
| `scripts/`、`.github/` | 本项目 | 随便改 |

---

## 2. 运行原理

```
PromptOptimizerApp.exe
   │
   ├─ 命名互斥量 ──────────── 保证只有一个实例（两个窗口写同一份 IndexedDB 会出问题）
   │
   ├─ 内置 HTTP 服务 ──────── 监听 127.0.0.1:17321，把打包进来的 web 产物当普通网站提供
   │
   ├─ WebView2 窗口 ───────── 加载 http://127.0.0.1:17321/（Edge 内核，Win10/11 自带）
   │
   └─ 页面加载后注入 ──────── 桌面标记 / 系统级 toast / 拖拽保护
```

### 为什么非要起一个本地服务器？

前端的全部数据——会话历史、收藏、模板、模型配置、图片缓存——都存放在浏览器的 **IndexedDB** 里，
而 IndexedDB 是按 **origin** 隔离的：

- 用 `file://` 打开：不同路径会被当成不同 origin，换个入口数据就"没了"；
- 用随机端口：每次启动 origin 都变，等于每次都是新用户；
- **固定端口 + 固定 origin**：`http://127.0.0.1:17321` 永远指向同一份数据。

所以端口默认写死 `17321`，配置里能改，但改了就等于换一份本地数据（配置项里也写了这条警告）。
端口被别的程序占用时会自动退到 17322…，此时会弹提示告知你本次看不到旧数据。

### 为什么关掉 Service Worker？

上游是 PWA，靠 Service Worker 做离线缓存。桌面端的产物本来就打在 exe 里，
留着 SW 只会造成"换了新 exe，窗口里还是旧页面"。所以服务端把 `/sw.js` 换成一个空壳脚本，
顺手清掉历史缓存（只清 Cache Storage，**不动 IndexedDB**，数据安全）。

### 生命周期与单实例

本地服务是 **exe 进程内的一个线程**，没有独立服务、没有系统服务注册、没有开机自启项：

- 启动：读配置 → 绑定端口起服务 → 创建窗口加载 `http://127.0.0.1:<port>/`
- 关闭：窗口关闭 → 停止服务 → 进程退出（强杀进程时由系统回收端口，不会留孤儿）
- 单实例：命名互斥量把关，重复双击不会开第二个进程，而是通过 `/__app/activate`
  让已有窗口从最小化恢复并置前

窗口标题形如 `Prompt Optimizer v1.0.1`，版本号来自 `desktop/app/version.py`（CI 打 tag 时以 tag 为准）。

---

## 3. 使用

### 直接下载

到 [Releases](https://github.com/CloverKing/PromptOptimizerApp/releases) 下载 `PromptOptimizerApp.exe`，双击即用，无需安装。

- 数据目录：`%LOCALAPPDATA%\PromptOptimizerApp`
- 配置：`%LOCALAPPDATA%\PromptOptimizerApp\config.json`
- 日志：`%LOCALAPPDATA%\PromptOptimizerApp\logs\app.log`

**便携模式**：在 exe 同级新建一个空文件 `portable.txt`（或一个 `data` 目录），数据就会写在 exe 旁边的 `data\` 里，适合放 U 盘。

### 从源码构建

前置：

- **Node.js 24.22+**（上游 `package.json` 的 `engines` 卡得很死，低版本 pnpm 直接拒绝构建）
- **pnpm 10**（`npm i -g pnpm@10`）
- **Python 3.11+**

```bash
git clone https://github.com/CloverKing/PromptOptimizerApp.git
cd PromptOptimizerApp

python -m pip install -r desktop/requirements.txt
python desktop/build.py
```

产物：`desktop/dist/PromptOptimizerApp.exe`。

常用变体：

```bash
python desktop/build.py --skip-pack    # 只编译前端，方便调页面
python desktop/build.py --skip-node    # 复用已有的 desktop/web-dist，只重新打包
```

只调桌面外壳时（不用每次等前端编译）：

```bash
cd desktop
python build.py --skip-pack    # 首次需要，生成 desktop/web-dist
python -m app --debug          # 开控制台日志 + DevTools
python -m app --server-only --port 17321   # 只起服务，用浏览器看效果
```

---

## 4. 同步上游更新

`upstream/` 是 subtree，同步就是一条命令：

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File scripts/sync-upstream.ps1 -Rebuild
```

```bash
# macOS / Linux / Git Bash
./scripts/sync-upstream.sh --rebuild
```

等价的手工操作：

```bash
git fetch upstream develop
git subtree pull --prefix=upstream upstream develop --squash
python desktop/build.py
```

**为什么不会冲突**：本项目从不修改 `upstream/` 里的任何文件，所有新增内容都在它之外。
subtree 做的只是"把上游树替换成新版"，与本地新增文件不相交。

同步后建议：

```bash
git tag v1.0.1 && git push origin main --tags   # 触发 CI 出 Release
```

上游版本号会写进 `desktop/build-info.json`，程序启动时记录到日志，也可通过
`http://127.0.0.1:<port>/__app/health` 查到。

---

## 5. 配置项

`%LOCALAPPDATA%\PromptOptimizerApp\config.json`（首次启动自动生成）：

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `port` | `17321` | 本地服务端口。**改端口等于换一份本地数据** |
| `window.width/height` | `1280/860` | 窗口尺寸，关闭时自动回写 |
| `window.maximized` | `true` | 启动时最大化；改成 `false` 用普通窗口（下次启动生效） |
| `disable_service_worker` | `true` | 关闭 PWA 缓存，保证升级后加载新前端 |
| `check_updates` | `true` | 启动后查一次本仓库 Release |
| `devtools` | `false` | 打开开发者工具 |
| `extra_browser_args` | `[]` | 追加给 WebView2 的参数 |

---

## 6. 与上游 Electron 桌面版的差异

上游仓库里也有 `packages/desktop`（Electron）。两者是不同取舍，不是替代关系：

| 能力 | 上游 Electron 版 | 本项目（Python + WebView2） |
| --- | --- | --- |
| 分发形态 | NSIS 安装包 + zip | **单个 exe**，免安装 |
| 体积 | 100 MB+ | 约 15 MB（不含系统 WebView2） |
| 数据存放 | 文件系统（userData 目录） | WebView2 的 IndexedDB |
| 存储后端 | 文件存储（可被 S3/WebDAV 备份） | IndexedDB（可用目录级备份） |
| 自动更新 | electron-updater 静默更新 | 检查 Release 后提示手动下载 |
| 内置代理设置 | 有（可配 HTTP 代理） | 无，跟随系统代理 |
| 远程备份（S3/WebDAV） | 有 | 无 |
| 运行时依赖 | 自带 Chromium | 系统 WebView2（Win10/11 默认自带） |
| 上游升级成本 | 跟随 merge 上游 package | `git subtree pull` + 重新打包 |

换句话说：**要"一个绿色 exe、体积小、升级简单"，用本项目；要"远程备份、内置代理、静默更新"，用上游 Electron 版。**

---

## 7. 常见问题

**首次启动有 1～2 秒黑屏？**
单文件 exe 需要先解压到临时目录，再初始化 WebView2。属正常现象。

**Windows 提示"已保护你的电脑"？**
exe 未做代码签名，SmartScreen 对新下载的未知程序都会拦。点"更多信息 → 仍要运行"。

**某些第三方 API 报跨域（CORS）错误？**
Web 模式下浏览器直接请求服务商接口，服务商必须允许浏览器直连（主流厂商都允许）。
遇到不支持的服务商，可在配置里加：

```json
{ "extra_browser_args": ["--disable-web-security"] }
```

这会降低窗口内的浏览器安全策略，请自行权衡；或者改用上游 Electron 版（它走主进程转发，没有 CORS 限制）。

**怎么备份/迁移数据？**
复制整个 `%LOCALAPPDATA%\PromptOptimizerApp\webview` 目录即可，换机器放回同路径就能接着用。

**怎么彻底卸载？**
删掉 exe，再删 `%LOCALAPPDATA%\PromptOptimizerApp`。

---

## 8. 许可

本项目以 **AGPL-3.0-only** 发布，与上游一致。详见 [LICENSE](LICENSE) 与 [NOTICE.md](NOTICE.md)。

上游版权：Copyright (C) 2025 linshenkx。
本封装层：Copyright (C) 2026 CloverKing。
