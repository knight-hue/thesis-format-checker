# 术语策略数据（运行时）

本目录只放**公开规范词表与口径**，不放论文正文、不放 300 条标注、不放 `private/parsed`。

## 文件

| 文件 | 来源 | 说明 |
| --- | --- | --- |
| `terminology_exemptions_2027.json` | `review/terminology_exemptions_2027.json` | 1112 条规范词表（A/B/C/D/E 级、章节、中文名、备注） |
| `terminology_policy.json` | `review/terminology_policy_overrides.json` + 离线 gate 集合 | 运行时口径、范围规则、旧 gate 通用词/专名、B04 配置 |

## 生成

```text
materials/semantic-real-20261110/review/terminology_exemptions_2027.json
materials/semantic-real-20261110/review/terminology_policy_overrides.json
materials/semantic-real-20261110/tools/judge_hybrid.py          # PROPER
materials/semantic-real-20261110/tools/build_samples.py         # UBIQUITOUS
materials/semantic-real-20261110/tools/resolve_unknown_by_occurrence.py
        ↓
backend/detection/semantic/data/terminology_exemptions_2027.json
backend/detection/semantic/data/terminology_policy.json
```

生成脚本只在本机运行，仓库运行时不读取 materials 路径。

## 口径要点

1. 检查范围：英文摘要 / 中文摘要 / 正文 / 致谢各自独立。
2. 条件豁免 `AI` 优先于旧 gate 的 `UBIQUITOUS`；严格模式下 `AI` 仍需解释。
3. 旧 gate 的 `PROPER` 集合用于专名 NA；`b04_excluded_terms` 只阻止 B04
   领域豁免，不覆盖专名判定。
4. 括号内容必须真正展开缩写；`Virtual DOM` / `virtualDOM模块` 不算；
   但 `（English Full Name，简称 TOKEN）`、`中文名（TOKEN）`、
   `TOKEN 是 <词表中文名>`、无括号的 `TOKEN+中文名` / `TOKEN+英文全名组件`
   都算释义。
5. 词表 A 级官方标识免解释；C 级核心术语首次出现无释义判违规；
   D 级官方专名默认不判违规，注明“首次宜说明”的除外。
6. 算法专名 `K-means` / `AdaBoost`、模型专名 `PSPNet`、官方格式名 `OOXML`
   不按“英文缩写首次出现”要求解释（2026-11-15 人审）；OOXML 建议补入 A 级源表。
7. `ECG` 保持 B04 条件豁免：默认关闭；开启且论文实际领域为医学/生命科学时
   才可转 NA，不能一律 NA。

## 更新流程

1. 先更新 materials 中的源文件与口径记录。
2. 重新生成两个 JSON，保持 `schema_version`/`source` 字段。
3. 跑 `tests/test_terminology_gate.py` 与 300 条 parity
   (`scripts/eval_terminology_gate.py`)。
4. 不在本目录手改词条；手改会与源文件漂移。
