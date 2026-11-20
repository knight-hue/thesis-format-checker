# frontend — 论文格式检查系统前端

基于 Vue 3 + TypeScript + Vite + Axios + Element Plus + Vue Router。

## 目录结构

```text
frontend
├── src
│   ├── api
│   │   ├── request.ts     # 公共 axios 实例：baseURL=/api、默认 30s 超时、响应统一解包
│   │   ├── parser.ts      # 异步检测任务创建、进度轮询与结果恢复
│   │   ├── fix.ts         # POST /fix（写回下载，独立实例以读取响应头）
│   │   ├── rag.ts         # POST /rag/answer、/rag/feedback、GET /colleges、POST /session/reset
│   │   └── system.ts      # GET /health
│   ├── components
│   │   ├── Sidebar.vue            # 全局侧边导航
│   │   ├── ChatPanel.vue          # 对话面板（RAG 问答）
│   │   ├── ScoreCard.vue          # 统计卡片
│   │   ├── ProblemCategoryCard.vue # 问题分类分布
│   │   ├── ErrorCard.vue          # 单条问题卡片（定位 + 解释入口）
│   │   ├── RagDialog.vue          # 规则解释 / 问答弹窗
│   │   ├── ExportButton.vue       # 检测报告导出
│   │   ├── FixBar.vue             # 「修复」操作条
│   │   └── ColorSwatches.vue      # 强调色选择（6 色）
│   ├── utils
│   │   ├── fixflow.ts     # 修复流程编排：修复 → 下载 → 基线前移 → 自动重检
│   │   ├── idb.ts         # IndexedDB 存取原始 docx（刷新后仍可继续修复）
│   │   └── issue.ts       # issue 字段归一化：注入 _id、标题/定位/等级/可修复性
│   ├── mock/index.ts      # 本地 Mock：axios adapter 拦截，脱离后端也能跑通
│   ├── views              # Home / Result / Detail / Help / Settings
│   ├── types              # 生成契约 / issue / chat / store
│   ├── router/index.ts    # 路由表
│   ├── store.ts           # 极简跨页共享状态（reactive 单例，非 Pinia）
│   ├── theme.ts           # 强调色 → CSS 变量（--accent-*；侧栏固定深蓝）
│   ├── App.vue            # 布局：Sidebar + 路由出口 + ChatPanel
│   ├── main.ts            # 挂载、注册 Element Plus 与全部图标
│   └── style.css
├── .env.development       # VITE_USE_MOCK / VITE_PROXY_TARGET
├── .env.production
├── package.json
├── tsconfig.json          # strict 类型检查，allowJs=false
├── tests/contracts.test.mjs # 使用 Node + Vite 的回归测试
└── vite.config.js         # @ → ./src；/api 代理到 VITE_PROXY_TARGET
```

## 页面与路由

| 路径 | 页面 | 职责 |
| --- | --- | --- |
| `/` | `Home.vue` | 先选学校、再选学院，上传 `.docx`、触发检测（上传 → 检测 → 查看结果三步指示） |
| `/result` | `Result.vue` | 检测结论 / 错误 / 提示 / 耗时四张统计卡、问题分类分布、模块明细与勾选修复、报告导出 |
| `/detail` | `Detail.vue` | 按定位逐条查看问题卡片，可唤起解释弹窗 |
| `/help` | `Help.vue` | 使用说明与常见问题 |
| `/settings` | `Settings.vue` | 强调色、语言（英文待开放）、清空当前检测会话 |

## 关键机制

- **跨页共享状态**：`store.ts` 是 `reactive` 单例，页面之间共享学院、文件名、issue 列表与勾选集合，不引入 Pinia。
- **刷新不丢结果**：会话 ID 与结果快照存 `sessionStorage`，原始 docx 存 IndexedDB（`utils/idb.ts`），强调色存 `localStorage`，刷新后结果页可原样还原并继续修复。
- **修复流程**（`utils/fixflow.ts`）：修复 → 下载修复稿 → 把修复稿当新基线（内存与 IndexedDB 一起换掉）→ 用修复稿自动重检。修复会向文档插入段落，`location.index` 会整体漂移，因此必须重检才能让清单下标对齐当前文档；基线前移让多轮修复自然叠加，用户不必反复上传。
- **可修复性**：每条 issue 带后端下发的 `fixable` / `fix_reason`，前端据此决定能否勾选，理由通过 tooltip 展示。

## 运行

```bash
npm ci
npm run typecheck # 严格类型检查
npm run dev       # 开发，默认代理到真实后端（Mock 关闭）
npm run build     # 生产构建；提交前请先单独运行 typecheck
npm run preview   # 预览静态构建产物（/api 需另行配置反向代理）
node --test tests/contracts.test.mjs # 契约、转义与快照回归
```

## 接口约定

Base URL 为 `/api`（在 `api/request.ts` 统一设置，页面里不写域名）；开发环境下 `/api` 由 Vite 代理转发到 `VITE_PROXY_TARGET`。

| 接口 | 调用处 | 说明 |
| --- | --- | --- |
| `POST /check-jobs/create` | `api/parser.ts` | multipart 上传论文并返回任务 ID；客户端预生成 `job_id`，创建响应超时后仍可恢复 |
| `POST /check-jobs/status` | `api/parser.ts` | 查询 `queued/running/succeeded/failed` 状态、处理阶段与 0–100 进度 |
| `POST /check-jobs/result` | `api/parser.ts` | 获取已完成任务的持久化检测结果；结果仍按最多 1000 个聚合问题返回 |
| `POST /check-file` | 后端兼容入口 | 旧同步上传检测接口，前端正常流程不再使用固定 180 秒请求 |
| `POST /fix` | `api/fix.ts` | multipart：`file`、`college_id`、`issues_json`、`conversation_id`；响应为修复后的 docx 文件流 |
| `POST /rag/answer` | `api/rag.ts` | 结构化对话回答（问题卡片、后续问题、执行轨迹） |
| `POST /rag/feedback` | `api/rag.ts` | 回答反馈（有用 / 没用 / 答非所问），只提交评分与元数据 |
| `GET /colleges` | `api/rag.ts` | 可选学院列表 |
| `POST /session/reset` | `api/rag.ts` | 清空指定会话的对话历史 |

- `parser.ts` 将待处理任务 ID 与论文暂存到 `sessionStorage` / IndexedDB。刷新页面或轮询请求暂时失败时，首页会继续查询原任务；任务成功后才把论文提升为后续修复使用的基线文件。
- `/fix` 单独建 axios 实例，因为要读响应头来做统计与列表同步：`X-Applied-Count` / `X-Skipped-Count` / `X-Fixed-Indexes`，以及写回阶段的跳过明细 `X-Writeback-Skip-Count` 与 `X-Writeback-Warnings`（JSON 数组，非 ASCII 已转义，可直接 `JSON.parse`）。
  - `X-Skipped-Count` 是**修复器**拒收的条数（不支持自动修复），`X-Writeback-Skip-Count` 是**写回阶段**接受但落不进去的条数（例如段落含域代码改不动、多节文档不支持结构性原位写回）。两者互不相同，且写回跳过项不计入 `X-Fixed-Indexes`。
  - 明细超过后端 2048 字符上限时只回 `X-Writeback-Skip-Count`，此时 `api/fix.ts` 解析不到明细数组，`utils/fixflow.ts` 退化为只提示条数。

## Mock 与联调切换

- `.env.development` 默认 `VITE_USE_MOCK=false`；显式开启 `VITE_USE_MOCK=true` 后：`request.ts` 与 `fix.ts` 会把 axios adapter 换成 `mock/index.ts` 的本地实现，无需后端即可跑通页面。
- Mock 覆盖异步检测创建/进度/结果、同步兼容检测、修复、RAG、健康检查、学院和会话接口；未定义的接口会直接报错提醒补 Mock。Mock 的 issue 结构与真实后端一致，并带稳定 `_id` 以支持修复后过滤。
- 联调时把 `VITE_USE_MOCK` 改为 `false`，并把 `VITE_PROXY_TARGET` 指向真实后端地址。

## TypeScript 与接口类型

业务代码全部使用 `.ts` / `<script setup lang="ts">`，`tsconfig.json` 开启 `strict`，关闭 `allowJs`。构建配置 `vite.config.js` 保持 JavaScript。

- `src/types/api.gen.ts`：由仓库内 `docs/openapi.json` 生成，禁止手工编辑；生成文件已保留，离线检查和构建不依赖后端服务。
- `src/types/issue.ts`：问题领域类型，保留后端 `actual/expected` 的 `unknown` 契约，避免转换数组或对象影响修复提交。
- `src/types/chat.ts`：回答、动作、澄清及安全排版类型。
- `src/types/store.ts`：全局状态、版本化结果快照和异步检测任务类型。

后端更新模型并导出 OpenAPI 快照后，在 `frontend/` 运行：

```powershell
npm run gen:api
npm run typecheck
npm run build
```

只新增固定版本的开发依赖：`typescript@5.9.3`、`vue-tsc@3.3.13`、`openapi-typescript@7.13.0`。现有运行依赖和 Vite 版本保持锁定。

Node 可使用项目 `.venv/nodejs` 下的便携版，也可使用满足项目版本要求的系统 Node。仓库根目录 `start.ps1` 的启动参数保持不变；不需要方案示例中的固定 `E:/thesis-format-checker` 路径。

## 对话交互与兼容性

聊天中的所有问题均请求 `/api/rag/answer`，携带同一个 `conversation_id`。前端不根据问题文本识别意图，也不拼装检测结论。

- 普通回答优先渲染后端 `blocks`：`summary` 为检测概览，`issues` 为逐条问题卡片（定位、要求、实测、建议、模板依据），`text` 为固定说明；卡片可高亮、追问“怎么改/依据”或按规则号核对。没有 `blocks` 的旧响应才回退到 `buildAnswerSections`。HTML 仅来自转义后的输出。
- 论文类意图除 `paper_overview/paper_detail/paper_fix` 外，还有 `paper_basis`（依据哪句模板）与 `paper_coverage`（哪些没查）。前端只展示后端给的 `suggestions`，不自己拼问法。
- `clarify` 展示“这是追问，不是结论”和选项；`multi_question` 会把一轮多个问题拆开，先让用户选一个。选择选项会作为同一会话的新发言发送。
- `suggestions` 是后端确认可回答的后续问法，点击即发送；会随实际数据变化（没有问题就不给“第 1 条怎么改”，还有更多才给“继续看”）。
- `response_id` 只用于反馈提交；`/api/rag/feedback` 只保存评分、意图和来源，不接收问题或回答正文。
- `open-result` 显示“查看检测结果”；`highlight-issues` 显示“高亮对应问题”，点击后打开结果页、展开分类并按 `targets`（`rule_id` + 定位）只高亮对应行；旧响应没有 `targets` 时退回按 `rule_id` 高亮。高亮只覆盖当前返回列表，不能代表被截断的全部问题。
- `paper_version` 是检测结果指纹：与最新回答不同时，消息会标注“基于旧检测结果”，提醒重新提问。
- 检测总数与分类汇总使用后端字段；“错误 / 提示”标明为当前返回列表的数量。
- `trace` 是状态机节点名列表（如 `router` → `paper_detail`），供排障与聊天评测使用，不展示论文内容。
- 新对话在后端重置成功后清空本地记录；失败时保留原对话并提示重试，避免前后端历史不同步。
- 旧响应缺少 `blocks/suggestions/response_id/trace` 时，`api/rag.ts` 补安全默认值：不伪造卡片，也不显示反馈按钮。
- 问答超时仍为 120 秒。知识库预热重试使用 HTTP 503 和 `/health` 状态区分加载中与失败，不按中文报错文本判断。修复仍须用户勾选确认，下载后使用修复稿自动重检。

## Mock 场景操作

无需修改或提交 `.env.development`，在 `frontend/` 的 PowerShell 中运行：

```powershell
$env:VITE_USE_MOCK = 'true'
npm run dev
# 停止后清理当前终端的覆盖项，再启动即可恢复真实后端联调：
Remove-Item Env:VITE_USE_MOCK
```

开启 Mock 后，在浏览器开发者工具 Console 选择场景，然后向 AI 助手发送任意问题：

```javascript
sessionStorage.setItem('thesis_mock_chat_case', 'overview')
// overview：论文概览 + 查看结果；detail：问题解释 + 高亮
// clarify：追问选项，点击选项后返回问题解释及高亮
// spec：三段式规范问答 + 来源；fix：修改建议 + 查看结果
// out_of_scope：范围外问题；legacy：旧后端响应，无新增字段
sessionStorage.removeItem('thesis_mock_chat_case') // 恢复默认 spec
```

Mock 使用合成示例，不能验证下载内容是有效修复稿，也不能验证真实模型答案质量。验证下载 DOCX 内容须关闭 Mock、连接真实后端并使用人工构造样张。375px 窄屏下导航改为顶部布局，结果双栏折叠，聊天抽屉不超过屏幕宽度。

## 本次文档备份

改造前文档逐字节备份于 [`../docs/backups/frontend-refactor-2026-10-23/`](../docs/backups/frontend-refactor-2026-10-23/)，包含根 README 和前端 README。
