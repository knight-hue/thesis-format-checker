# 待测样张目录（真实学生论文）

把要测试的 **学生论文 Word 文档** 放进本目录（.docx 格式），然后运行：

```powershell
.\.venv\Scripts\python.exe -m eval.run            # 全链路（含本目录文档）
.\.venv\Scripts\python.exe -m eval.run --stage real  # 只跑真实样张
.\.venv\Scripts\python.exe -m eval.run --samples-dir D:\papers  # 指定其它目录
```

## 说明

- 只支持 **.docx**（新版 Word 格式）；旧版 **.doc** 无法解析，会被跳过并在报告中注明。
- 文档会逐个执行：解析 → 检测 → 修复 → 写回，单篇出错不影响其余文档。
- **无人工金标准**：不计算精确率/召回率，报告只统计检出问题分布、修复结果与耗时。
- 文档内含姓名学号等个人信息，本目录已加入 `.gitignore`，不会提交到仓库。
- 报告输出到 `eval/out/test_<时间戳>/`，与合成样张评测同一份报告展示。
