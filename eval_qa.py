# -*- coding: utf-8 -*-
"""RAG 问答系统准确性评估脚本。

对规则原文覆盖的每个小节构造事实型问题，逐条调用正在运行的后端
POST /api/rag/answer，计算：
  1. 检索命中率 Hit@1 / Hit@5 —— 正确小节是否进入 top-K 召回（客观指标）
  2. found 率 —— 系统是否判定"能找到答案"
  3. 答案事实覆盖率 —— 结论+依据中是否包含预期关键事实（近似指标）
并把完整问答结果落盘到 eval_results/，供人工复核。

用法：先启动后端（uvicorn api:app），再执行：
    .venv/Scripts/python.exe eval_qa.py [jlu_cs|jlu_se|all]
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime

# Windows 控制台默认 GBK，打印 ▶ 等字符会抛 UnicodeEncodeError，统一改为 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "http://127.0.0.1:8000"
API = BASE_URL + "/api/rag/answer"

# ---------------------------------------------------------------------------
# 测试集
# section：答案所在小节号（用于检索命中判定，与 rules/*.txt 的【第X章 / X.X】对应）
# q：自然语言问题
# facts：正确答案（结论+依据）应包含的关键事实；facts_se 仅当软件学院答案不同时覆盖
# kind：fact=事实题 / reverse=反向题 / multi=多问题 / ambiguous=歧义题 / out=越界题
# ---------------------------------------------------------------------------
QUESTIONS = [
    # 第1章 页面设置
    {"section": "1.1", "q": "论文纸张大小是什么规格？", "facts": ["A4"], "kind": "fact"},
    {"section": "1.1", "q": "左页边距是多少厘米？", "facts": ["3厘米"], "kind": "fact"},
    {"section": "1.1", "q": "页眉边距是多少？", "facts": ["1.5厘米"], "kind": "fact"},
    {"section": "1.2", "q": "正文行距是多少倍？", "facts": ["1.5倍"], "kind": "fact"},
    {"section": "1.2", "q": "段落首行缩进几个字符？", "facts": ["2字符"], "kind": "fact"},
    {"section": "1.2", "q": "正文的对齐方式是什么？", "facts": ["两端对齐"], "kind": "fact"},

    # 第2章 摘要与关键词
    {"section": "2.1", "q": "中文题目用什么字体、什么字号？", "facts": ["黑体", "小三"], "facts_se": ["宋体", "小三"], "kind": "fact"},
    {"section": "2.1", "q": "摘要标题用什么字体、什么字号？", "facts": ["黑体", "三号"], "facts_se": ["宋体", "四号"], "kind": "fact"},
    {"section": "2.2", "q": "中文摘要正文用什么字体、什么字号？", "facts": ["宋体", "小四"], "kind": "fact"},
    {"section": "2.3", "q": "中文关键词标题用什么字体、什么字号？", "facts": ["黑体", "四号"], "facts_se": ["宋体", "四号"], "kind": "fact"},
    {"section": "2.4", "q": "关键词一般要几个？", "facts": ["3", "5"], "kind": "fact"},
    {"section": "2.4", "q": "关键词之间用什么符号分隔？", "facts": ["逗号"], "kind": "fact"},
    {"section": "2.5", "q": "Abstract标题用什么字体、什么字号？", "facts": ["Times New Roman", "小三"], "facts_se": ["Times New Roman", "四号"], "kind": "fact"},
    {"section": "2.6", "q": "英文摘要正文用什么字体、什么字号？", "facts": ["Times New Roman", "小四"], "kind": "fact"},
    {"section": "2.7", "q": "Keywords标题用什么字体、什么字号？", "facts": ["Times New Roman", "四号"], "kind": "fact"},

    # 第3章 标题层级
    {"section": "3.1", "q": "一级标题用什么字体、什么字号？", "facts": ["黑体", "三号"], "facts_se": ["宋体", "三号"], "kind": "fact"},
    {"section": "3.2", "q": "二级标题用什么字体、什么字号？", "facts": ["黑体", "四号"], "facts_se": ["宋体", "四号"], "kind": "fact"},
    {"section": "3.3", "q": "三级标题用什么字体、什么字号？", "facts": ["黑体", "小四"], "facts_se": ["宋体", "小四"], "kind": "fact"},
    {"section": "3.4", "q": "列表的总项序号采用什么格式？", "facts": ["1．"], "kind": "fact"},

    # 第4章 正文格式
    {"section": "4.1", "q": "正文中文用什么字体？", "facts": ["宋体"], "kind": "fact"},
    {"section": "4.1", "q": "正文中的英文和数字用什么字体？", "facts": ["Times New Roman"], "kind": "fact"},
    {"section": "4.2", "q": "标点符号用全角还是半角？", "facts": ["全角"], "kind": "fact"},
    {"section": "4.3", "q": "外文缩写首次出现时应该怎样处理？", "facts": ["括号", "含义"], "kind": "fact"},
    {"section": "4.4", "q": "量和单位采用什么国家标准？", "facts": ["GB3100"], "kind": "fact"},
    {"section": "4.5", "q": "测量统计数据用什么数字书写？", "facts": ["阿拉伯数字"], "kind": "fact"},
    {"section": "4.6", "q": "注释标记用什么开头？", "facts": ["【注】"], "kind": "fact"},

    # 第5章 图表格式
    {"section": "5.1", "q": "图和表按什么规则编号？", "facts": ["章节"], "kind": "fact"},
    {"section": "5.2", "q": "图应该放在相关文字说明之前还是之后？", "facts": ["之后"], "kind": "fact"},
    {"section": "5.3", "q": "图号和图题名放在图的什么位置？", "facts": ["下方"], "kind": "fact"},
    {"section": "5.3", "q": "图名用什么字体、什么字号？", "facts": ["黑体", "五号"], "kind": "fact"},
    {"section": "5.4", "q": "表号放在表的上方还是下方？", "facts": ["上方"], "kind": "fact"},
    {"section": "5.5", "q": "表号表名排于表的什么位置？", "facts": ["上方"], "kind": "fact"},
    {"section": "5.5", "q": "表名用什么字体、什么字号？", "facts": ["黑体", "五号"], "kind": "fact"},

    # 第6章 公式格式
    {"section": "6.1", "q": "公式在页面中如何排列？", "facts": ["居中"], "kind": "fact"},
    {"section": "6.1", "q": "公式编号放在什么位置？", "facts": ["右边"], "kind": "fact"},
    {"section": "6.2", "q": "有数字标注的坐标图必须注明什么？", "facts": ["单位"], "kind": "fact"},

    # 第7章 参考文献
    {"section": "7.1", "q": "参考文献序号用什么表示？", "facts": ["方括号"], "kind": "fact"},
    {"section": "7.2", "q": "期刊文章用什么类型标识？", "facts": ["[J]"], "kind": "fact"},
    {"section": "7.2", "q": "学位论文用什么类型标识？", "facts": ["[D]"], "kind": "fact"},
    {"section": "7.3", "q": "连续出版物的参考文献格式中包含什么信息？", "facts": ["卷号"], "kind": "fact"},
    {"section": "7.4", "q": "专著的参考文献格式中包含什么信息？", "facts": ["出版地"], "kind": "fact"},
    {"section": "7.5", "q": "学位论文的参考文献格式中包含什么信息？", "facts": ["保存地"], "kind": "fact"},
    {"section": "7.6", "q": "参考文献采用什么引用制度？", "facts": ["顺序编码制"], "kind": "fact"},

    # 第8章 致谢
    {"section": "8.1", "q": "致谢正文用什么字体、什么字号？", "facts": ["宋体", "小四"], "kind": "fact"},
    {"section": "8.2", "q": "致谢可以包括哪些内容？", "facts": ["基金"], "kind": "fact"},

    # 第9章 目录
    {"section": "9.1", "q": "目录内容用什么字号？", "facts": ["小四"], "facts_se": ["四号"], "kind": "fact"},
    {"section": "9.2", "q": "目录需要包含哪些内容？", "facts": ["致谢"], "kind": "fact"},

    # 特殊/困难用例（单独归类，不计入事实题命中率）
    {"section": "3", "q": "三号字用在哪些标题上？", "facts": [], "kind": "reverse"},
    {"section": "5", "q": "图题和表题分别在图的什么位置，字号各是多少？", "facts": [], "kind": "multi"},
    {"section": "3", "q": "标题的字号是多少？", "facts": [], "kind": "ambiguous"},
    {"section": "", "q": "今天天气怎么样？", "facts": [], "kind": "out"},
]


def norm(s: str) -> str:
    """归一化：小写、去空白、全角 ASCII 转半角，用于关键词匹配。"""
    s = s.lower()
    s = s.replace(" ", "").replace("　", "")
    out = []
    for ch in s:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:  # 全角 ! ~ 区间 → 半角
            out.append(chr(code - 0xFEE0))
        elif ch == "。":  # 。
            out.append(".")
        else:
            out.append(ch)
    return "".join(out)


def facts_for(item, college_id):
    if college_id == "jlu_se" and "facts_se" in item:
        return item["facts_se"]
    return item.get("facts", [])


def ask(college_id, question, timeout=120):
    body = json.dumps({"college_id": college_id, "question": question}).encode("utf-8")
    req = urllib.request.Request(
        API, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def section_hit(sources, section_id):
    """正确小节是否出现在召回结果里。"""
    for src in sources:
        sec = (src.get("section") or "").strip()
        token = sec.split()[0] if sec else ""
        if token == section_id:
            return True
    return False


def run_college(college_id):
    results = []
    for i, item in enumerate(QUESTIONS, 1):
        facts = facts_for(item, college_id)
        start = time.time()
        try:
            r = ask(college_id, item["q"])
            err = None
        except Exception as exc:  # noqa: BLE001
            r = None
            err = f"{type(exc).__name__}: {exc}"
        wall = time.time() - start

        rec = {
            "id": item["section"] + "." + str(i),
            "kind": item["kind"],
            "section": item["section"],
            "question": item["q"],
            "facts": facts,
            "found": None,
            "conclusion": "",
            "basis": "",
            "sources": [],
            "error": err,
            "total_time": wall,
        }
        if r is not None:
            rec["found"] = bool(r.get("found"))
            rec["conclusion"] = r.get("conclusion", "")
            rec["basis"] = r.get("basis", "")
            rec["sources"] = [
                {
                    "section": s.get("section", ""),
                    "full_path": s.get("full_path", ""),
                    "preview": s.get("preview", ""),
                }
                for s in (r.get("sources") or [])
            ]
            rec["total_time"] = r.get("total_time", wall)

        # 检索命中
        rec["hit5"] = section_hit(rec["sources"], item["section"]) if rec["sources"] else False
        rec["hit1"] = bool(rec["sources"]) and (
            (rec["sources"][0].get("section") or "").strip().split()[0] == item["section"]
        ) if rec["sources"] else False

        # 事实覆盖
        if facts:
            text = norm(rec["conclusion"] + " " + rec["basis"])
            covered = [f for f in facts if norm(f) in text]
            rec["fact_covered"] = len(covered)
            rec["fact_total"] = len(facts)
            rec["fact_ok"] = len(covered) == len(facts)
        else:
            rec["fact_covered"] = rec["fact_total"] = 0
            rec["fact_ok"] = None

        results.append(rec)
        print(f"  [{i}/{len(QUESTIONS)}] {item['section']:>4} {item['q']}"
              f"  hit5={rec['hit5']} found={rec['found']}"
              + (f" fact={rec['fact_covered']}/{rec['fact_total']}" if facts else "")
              + (f"  ERR={err}" if err else ""))

    return results


def summarize(college_id, results):
    fact_items = [r for r in results if r["kind"] == "fact"]
    special = [r for r in results if r["kind"] != "fact"]

    n = len(fact_items)
    hit5 = sum(r["hit5"] for r in fact_items)
    hit1 = sum(r["hit1"] for r in fact_items)
    found = sum(bool(r["found"]) for r in fact_items)
    fact_ok = sum(1 for r in fact_items if r["fact_ok"] is True)
    fact_with = [r for r in fact_items if r["fact_total"] > 0]
    fact_ok2 = sum(1 for r in fact_with if r["fact_ok"] is True)
    fact_cov = sum(r["fact_covered"] for r in fact_with)
    fact_tot = sum(r["fact_total"] for r in fact_with)
    avg_time = sum(r["total_time"] for r in fact_items) / n if n else 0

    lines = []
    lines.append(f"===== {college_id} =====")
    lines.append(f"事实题数量            : {n}")
    lines.append(f"检索命中率 Hit@5      : {hit5}/{n} = {hit5/n*100:.1f}%")
    lines.append(f"检索命中率 Hit@1      : {hit1}/{n} = {hit1/n*100:.1f}%")
    lines.append(f"found 率（判为可答）   : {found}/{n} = {found/n*100:.1f}%")
    lines.append(f"答案事实全覆盖率      : {fact_ok2}/{len(fact_with)} = {fact_ok2/len(fact_with)*100:.1f}%"
                 f"（关键事实覆盖率 {fact_cov}/{fact_tot} = {fact_cov/fact_tot*100:.1f}%）")
    lines.append(f"平均响应耗时          : {avg_time*1000:.0f} ms")
    lines.append("")
    miss = [r for r in fact_items if not r["hit5"]]
    if miss:
        lines.append(f"检索未命中（Hit@5 失败）的 {len(miss)} 条：")
        for r in miss:
            got = "、".join((s.get("section") or "?") for s in r["sources"][:5]) or "无"
            lines.append(f"  - [{r['section']}] {r['question']}  实际召回: {got}")
    err = [r for r in results if r["error"]]
    if err:
        lines.append(f"报错 {len(err)} 条：")
        for r in err:
            lines.append(f"  - [{r['section']}] {r['question']}  {r['error']}")
    lines.append("")
    return "\n".join(lines)


def main():
    targets = sys.argv[1:] if len(sys.argv) > 1 else ["all"]
    if targets == ["all"]:
        colleges = ["jlu_cs", "jlu_se"]
    else:
        colleges = [t for t in targets if t in ("jlu_cs", "jlu_se")]

    out_dir = os.path.join("eval_results", datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)

    summary_all = []
    for college in colleges:
        print(f"\n▶ 开始评估 {college} ...")
        results = run_college(college)
        with open(os.path.join(out_dir, f"{college}.json"), "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        summary_all.append(summarize(college, results))

    print("\n" + "=" * 60)
    print("\n".join(summary_all))
    print(f"完整结果已保存到：{out_dir}")


if __name__ == "__main__":
    main()
