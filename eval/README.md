# 评测系统（eval/）

对论文格式校验系统做**准确性**与**性能**的自动化评测，输出 JSON 原始数据 +

HTML 可视化报告。

## 快速开始



```
.\.venv\Scripts\python.exe -m eval.run                 # 全链路（准确性 + 性能 + 真实样张）
.\.venv\Scripts\python.exe -m eval.run --stage accuracy # 只跑合成准确性
.\.venv\Scripts\python.exe -m eval.run --stage real     # 只跑真实样张（自由导入的 Word 文档）
.\.venv\Scripts\python.exe -m eval.run --stage perf     # 只跑性能
.\.venv\Scripts\python.exe -m eval.run --keep-artifacts # 保留合成样张 docx 便于复查
.\.venv\Scripts\python.exe -m eval.run --samples-dir .\我的论文  # 指定真实样张目录
.\.venv\Scripts\python.exe -m eval.run --report-dir .\eval\out\custom  # 指定输出目录
```

**结果留档**：每次运行自动输出到 `eval/out/` 下两个**分类目录**，各自按
`test_<时间戳>/` 留档，历史结果不会被覆盖：

| 目录 | 内容 | 文件 |
| --- | --- | --- |
| `eval/out/synthetic/test_<时间戳>/` | 合成样张全链路报告（检测准确性 / 解析准确性 / 修复与写回 / 性能基准） | `eval_report.json`（原始数据，可审计）、`eval_report.html`（可视化） |
| `eval/out/real/test_<时间戳>/` | 真实样张（学生论文）测试报告，汇总页 + 每篇文档独立报告 | `index.html`（汇总）、`index.json`、每篇 `<文档名>.html` + `<文档名>.json` |

两个 HTML 报告均为**单文件自包含**：ECharts 图表库已内嵌（约 1MB），
不依赖任何外部 CDN，断网/内网环境双击即可正常查看。

## 评测设计

### 准确性：合成样张 + 金标准



* **合规基线**（`synthetic/baseline.py`）：按 `config/rules/jlu/cs/jlu_cs.txt`

  规范原文逐条构建的 "应当零问题" 论文（页边距、题目 / 摘要 / 关键词、多级标题、

  图与表、参考文献、致谢）。

* **错误注入**（`synthetic/injectors.py`）：每个注入器对基线做**恰好一个**

  已知破坏，并登记金标准（rule\_id + 定位 + 文本锚）。

* **指标**（`metrics.py`，微平均）：


  * 解析层：kind 识别准确率、文本保真；

  * 检测层：精确率 P / 召回率 R / F1，按 rule\_id 与严重级别分层；

  * 修复层：修复应用率、重检消失率、回归破坏率；

  * 写回层：DOCX 可打开率、未修改部件一致性。

匹配口径：规则引擎类按 `rule_id + (type,index,run,row,col)`；语义类

（location 无 index）按文本锚。金标准定位解析失败（结构识别没把注入点认成

期望类型）记为 FN 并在报告中标注。

### 真实样张：自由导入的学生论文

把待测 Word 文档放进 `eval/samples/`（或 `--samples-dir <路径>` 指定其它目录），
评测会对每个文档独立执行 **解析 → 检测 → 修复 → 写回**：

* 只支持 `.docx`；旧版 `.doc` 会被跳过并在报告中注明；
* 单篇出错只记录到该篇，不影响其余文档；
* 修复成功会生成 `<文档名>_已修复.docx` 保留在待测目录，供人工比对；
* **无人工金标准** → 不计算精确率/召回率，只统计关键指标：检出问题总数、
  问题分布（按规则、按严重级别）、修复应用率、写回保真与耗时；
* 报告与合成样张**分离**：汇总页 `index.html`（逐文档表格可点入）+ 每篇文档
  独立 `<文档名>.html`，按 `eval/out/real/test_<时间戳>/` 留档。

### 性能：单机基准



* 分阶段耗时：解析 / 检测 / 写回 / 端到端（多次取中位数）；

* 规模扫描：10/50/100/200 页近似论文 → 耗时曲线 + tracemalloc 内存峰值；

* AI 预热探测：LLMService / RAGService 初始化状态与耗时（无 API Key 或

  缺模型资产时如实记录为 failed/skipped，不影响其余评测）。

## 约束与局限



* 语义条款只覆盖**无需 LLM 的确定性条款**（致谢时间段、文献类型标识、

  计量单位、汉字数字）；生成式条款需 API Key 与知识库，未配置时不计入指标。

* 合成样张不覆盖全部真实形态（列表、公式、目录、封面等），指标对真实场景的

  代表性需配合真实样张人工标注（`eval/README` 后续可扩展真实样张目录）。

* **与结构识别耦合、无法用合成单点注入干净覆盖的规则**（识别与检查共用同一

  判据，注入会连带改变段落 kind，导致对应检查不触发、被误记为 FN）：

  `title.alignment`（题目改左对齐后识别降级为 cover\_info）、

  `heading.heading_2.number` / `heading.heading_3.number`（编号格式即识别判据）、

  `figure.caption_number`（图题编号格式即题注识别判据）。这类规则应放入真实

  样张人工标注库评测，合成样张只做检测层覆盖。

## 目录结构



```
eval/
├── run.py               # 入口
├── config.py            # 评测配置
├── golden.py            # 金标准数据结构与文本锚匹配
├── metrics.py           # P/R/F1、混淆矩阵、分组统计
├── synthetic/           # 合规基线 + 注入器 + 语料编排
├── runner/              # 解析/检测/修复/真实样张评测执行器
├── perf/                # 分阶段计时 + 规模扫描
├── samples/             # 真实样张目录：把待测 Word 文档放这里（README 见文件内说明）
└── report/              # JSON 与 HTML 报告（含真实样张汇总页 + 单篇报告）
```