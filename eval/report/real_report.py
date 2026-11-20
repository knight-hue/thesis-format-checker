# -*- coding: utf-8 -*-
"""真实样张（自由导入的 Word 文档）报告：汇总 index + 每篇文档一份 HTML/JSON。

设计原则（与合成样张报告分离）：
- 真实文档没有金标准，不计算 精确率/召回率，只呈现关键指标：
  检出问题总数、问题分布（按规则/严重级别）、修复应用、写回保真、耗时。
- 每篇文档一个独立 HTML（自包含、内嵌 ECharts），汇总页 index.html 列出全部文档并可点入单篇。
- 输出到 real 报告目录：默认 eval/out/real/test_<时间戳>/。
"""
import json
from pathlib import Path

from ..config import EvalConfig

_PAGE_TITLE = "真实样张测试报告"

# 与合成报告共用同一份内嵌 ECharts
_ECHARTS_PATH = Path(__file__).resolve().parent / "echarts.min.js"


def _inline_echarts() -> str:
    js = _ECHARTS_PATH.read_text(encoding="utf-8")
    return js.replace("</script", "<\\/script")


def render_real_reports(report: dict, config: EvalConfig) -> list:
    """生成全部真实样张报告文件，返回生成的文件路径列表。"""
    real = report.get("real") or {}
    if real.get("skipped"):
        return []

    out_dir = Path(config.report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    # 1) 汇总 JSON + HTML
    index_data = _build_index_data(report, real)
    (out_dir / "index.json").write_text(
        json.dumps(index_data, ensure_ascii=False, indent=2), encoding="utf-8")
    written.append(out_dir / "index.json")
    index_html = _INDEX_TEMPLATE.replace(
        "/*__ECHARTS_INLINE__*/", _inline_echarts())
    index_html = index_html.replace(
        "__RAW_DATA__", json.dumps(index_data, ensure_ascii=False))
    (out_dir / "index.html").write_text(index_html, encoding="utf-8")
    written.append(out_dir / "index.html")

    # 2) 每篇文档一个 HTML + JSON
    for doc in real.get("documents", []):
        stem = Path(doc["name"]).stem or "文档"
        (out_dir / f"{stem}.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(out_dir / f"{stem}.json")
        doc_html = _DOC_TEMPLATE.replace(
            "/*__ECHARTS_INLINE__*/", _inline_echarts())
        doc_html = doc_html.replace(
            "__RAW_DATA__", json.dumps(doc, ensure_ascii=False))
        doc_html = doc_html.replace("__PAGE_TITLE__", f"{stem} · 真实样张测试报告")
        (out_dir / f"{stem}.html").write_text(doc_html, encoding="utf-8")
        written.append(out_dir / f"{stem}.html")

    return written


def _build_index_data(report: dict, real: dict) -> dict:
    docs = real.get("documents", [])
    summary = []
    for d in docs:
        f = d.get("fix") or {}
        summary.append({
            "name": d.get("name"),
            "size_bytes": d.get("size_bytes"),
            "blocks": d.get("blocks"),
            "issue_count": d.get("issue_count"),
            "submitted": f.get("submitted", 0),
            "applied": f.get("applied", 0),
            "regressions": f.get("regressions", 0),
            "remaining_after_fix": f.get("remaining_after_fix"),
            "openable": f.get("openable"),
            "parts_preserved": f.get("parts_preserved"),
            "error": d.get("error"),
            "parse_ms": d.get("parse_ms"),
            "detect_ms": d.get("detect_ms"),
            "fix_ms": d.get("fix_ms"),
        })
    return {
        "meta": report.get("meta", {}),
        "samples_dir": real.get("samples_dir"),
        "totals": real.get("totals", {}),
        "fix_success_rate": real.get("fix_success_rate"),
        "by_rule": real.get("by_rule", {}),
        "by_severity": real.get("by_severity", {}),
        "documents": summary,
    }


# ============ 单篇文档模板 ============
_DOC_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__PAGE_TITLE__</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Crect width='24' height='24' rx='5' fill='%231F5E8E'/%3E%3Cpath d='M6 17V7h3v7h2V7h3v7h2V7h3v10z' fill='%23F6F4EF'/%3E%3C/svg%3E">
<link rel="stylesheet" href="https://miaoda.feishu.cn/fonts/css2?family=Noto+Sans+SC:wght@400;500;600;700&family=Noto+Serif+SC:wght@600;700;900&display=swap">
<script>/*__ECHARTS_INLINE__*/</script>
<style>
:root{
  --bg:#F6F4EF; --card:#FFFFFF; --ink:#17263A; --muted:#5A6B7C;
  --blue:#1F5E8E; --teal:#0E7C7B; --amber:#C47A1F; --red:#B3453A;
  --grid:#E5E1D8; --line:#D8D2C6; --chip:#EFECE4;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font:400 15px/1.65 "Noto Sans SC",system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:28px 20px 80px}
header.hero{border-bottom:1px solid var(--line);padding-bottom:24px;margin-bottom:28px}
.kicker{font-size:12px;letter-spacing:.18em;color:var(--blue);font-weight:600}
h1{font-family:"Noto Serif SC",serif;font-size:26px;line-height:1.35;margin:8px 0 6px;font-weight:700;word-break:break-all}
.sub{color:var(--muted);font-size:13px}
.grid{display:grid;gap:14px}
.cards4{grid-template-columns:repeat(4,1fr)}
@media(max-width:860px){.cards4{grid-template-columns:repeat(2,1fr)}}
@media(max-width:520px){.cards4{grid-template-columns:1fr}.wrap{padding:18px 12px 60px}h1{font-size:22px}}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;min-width:0}
.stat .v{font-size:28px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.2}
.stat .l{font-size:13px;color:var(--muted);margin-top:4px}
.stat .d{font-size:12px;color:var(--muted);margin-top:2px}
.stat.ok .v{color:var(--teal)} .stat.warn .v{color:var(--amber)} .stat.bad .v{color:var(--red)} .stat.blue .v{color:var(--blue)}
section{margin-top:40px}
h2{font-family:"Noto Serif SC",serif;font-size:20px;font-weight:700;margin:0 0 14px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin-top:14px}
.panel h3{font-size:15px;margin:0 0 12px;font-weight:600}
.chart{width:100%;height:320px}
table{width:100%;border-collapse:collapse;table-layout:fixed;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--grid);vertical-align:top;overflow-wrap:break-word}
th{color:var(--muted);font-weight:600;font-size:12px;letter-spacing:.02em;white-space:nowrap}
tr:last-child td{border-bottom:none}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap}
.pill.ok{background:#E2F0EE;color:var(--teal)} .pill.warn{background:#F7EBD9;color:var(--amber)} .pill.bad{background:#F5E2DF;color:var(--red)} .pill.mut{background:var(--chip);color:var(--muted)} .pill.blue{background:#E3ECF4;color:var(--blue)}
.note{font-size:12px;color:var(--muted);margin-top:8px}
.okline{font-size:13px;color:var(--teal);font-weight:600;margin:6px 0 0}
.mono{font-variant-numeric:tabular-nums;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
.en{color:var(--muted);font-size:.9em}
</style>
</head>
<body>
<div class="wrap">

<header class="hero">
  <div class="kicker">真实样张测试 · 单篇报告</div>
  <h1 id="doc-title"></h1>
  <div class="sub" id="hero-sub"></div>
  <div class="grid cards4" id="cards" style="margin-top:22px"></div>
  <p class="note" id="integrity" style="margin-top:12px"></p>
</header>

<section>
  <h2>检出问题分布</h2>
  <div class="panel"><h3>按规则统计（规则编号 rule_id）</h3><div class="chart" id="chart-rule"></div>
    <p class="note" id="severity-note"></p></div>
</section>

<section>
  <h2>问题明细</h2>
  <div class="panel"><div id="issue-table"></div></div>
</section>

<section>
  <h2>处理耗时</h2>
  <div class="panel"><div id="timing"></div></div>
</section>

</div>
<script>
const DATA = __RAW_DATA__;
const $ = (id) => document.getElementById(id);
const fmt = (v,d=3,def='—') => (v===null||v===undefined)?def:(typeof v==='number'?(Number.isInteger(v)?String(v):v.toFixed(d)):String(v));
function statCard(title, value, cls, desc){
  return `<div class="stat ${cls}"><div class="v">${value}</div><div class="l">${title}</div>${desc?`<div class="d">${desc}</div>`:''}</div>`;
}
const pill = (ok, okText='通过', badText='异常') => ok===true?`<span class="pill ok">${okText}</span>`:(ok===false?`<span class="pill bad">${badText}</span>`:'<span class="pill mut">—</span>');

function init(){
  $('doc-title').textContent = DATA.name||'未知文档';
  const kb = DATA.size_bytes==null?'—':(DATA.size_bytes/1024).toFixed(0)+' KB';
  $('hero-sub').textContent = `大小 ${kb} · 文本块（blocks）${fmt(DATA.blocks,0)} 个 · 解析 ${fmt(DATA.parse_ms,1)} ms · 检测 ${fmt(DATA.detect_ms,1)} ms`;
  const f = DATA.fix||{};
  const rem = f.remaining_after_fix;
  const issueTotal = DATA.issue_count||0;
  $('cards').innerHTML =
    statCard('检出问题总数', fmt(issueTotal,0), issueTotal>0?'warn':'ok', '检测引擎报出的格式问题条数') +
    statCard('可自动修复', fmt(f.submitted,0), 'blue', '检测出的问题中，有修复器支持的数量') +
    statCard('已应用修复', fmt(f.applied,0), (f.applied||0)>0?'ok':'mut', `修复接口实际应用的条数`) +
    statCard('修复后剩余', rem==null?'—':fmt(rem,0), (rem||0)===0?'ok':'warn', '修复后重新检测仍然存在的问题');
  $('integrity').innerHTML =
    `写回保真：${pill(f.openable,'可打开','损坏')} ${pill(f.parts_preserved,'未改动部件一致','有部件变更')} ` +
    `· 回归（修复引入新问题）：<span class="pill ${(f.regressions||0)>0?'bad':'ok'}">${fmt(f.regressions,0)} 条</span>` +
    (f.fix_error?` · <span class="pill bad">${String(f.fix_error).slice(0,80)}</span>`:'') +
    (DATA.error?`<br><span class="pill bad">处理出错：${String(DATA.error).slice(0,120)}</span>`:'');
  // 按规则分布
  const agg = {};
  for(const i of (DATA.issues||[])){
    const rid = i.rule_id||'(未知)';
    agg[rid]=(agg[rid]||0)+1;
  }
  const names = Object.keys(agg).sort((a,b)=>agg[b]-agg[a]);
  const el=$('chart-rule');
  if(window.echarts && names.length){
    const chart=echarts.init(el);
    chart.setOption({grid:{left:190,right:50,top:20,bottom:40},
      tooltip:{trigger:'axis',axisPointer:{type:'shadow'},confine:true},
      xAxis:{type:'value',minInterval:1,name:'条数'},
      yAxis:{type:'category',data:names,axisLabel:{fontSize:11,fontFamily:'ui-monospace,Consolas,monospace'}},
      series:[{type:'bar',data:names.map(n=>agg[n]),itemStyle:{color:'#1F5E8E'},barMaxWidth:26,label:{show:true,position:'right'}}]});
    window.addEventListener('resize',()=>chart.resize());
  } else el.textContent='无检出问题数据';
  const sev={};
  for(const i of (DATA.issues||[])){ const s=i.severity||'unknown'; sev[s]=(sev[s]||0)+1; }
  $('severity-note').textContent = `按严重级别：${Object.keys(sev).map(k=>`${k} ${sev[k]} 条`).join('，')||'无'}。`;
  // 问题明细
  const issues = DATA.issues||[];
  const tt=$('issue-table');
  if(!issues.length){
    tt.innerHTML = '<p class="okline">未检出任何问题。</p>';
  } else {
    const st = (i)=>{
      if(f.fix_error) return '<span class="pill bad">修复出错</span>';
      if(i.fixable && f.submitted>0) return (f.applied>=f.submitted)?'<span class="pill ok">已修复</span>':'<span class="pill warn">部分跳过</span>';
      if(i.fixable) return '<span class="pill mut">待修复</span>';
      return '<span class="pill mut">不可自动修复</span>';
    };
    tt.innerHTML = `<table><colgroup><col style="width:24%"><col style="width:10%"><col style="width:44%"><col style="width:10%"><col style="width:12%"></colgroup><thead><tr><th>规则编号（rule_id）</th><th>严重级别</th><th>说明</th><th>状态</th><th>是否可自动修复</th></tr></thead><tbody>${issues.map(i=>{
      const s=i.severity||'unknown';
      const sp = s==='error'?'<span class="pill bad">错误 error</span>':(s==='warning'?'<span class="pill warn">警告 warning</span>':'<span class="pill mut">'+s+'</span>');
      return `<tr><td class="mono">${i.rule_id||'—'}</td><td>${sp}</td><td>${i.message||''}${i.actual?`<div class="note" style="margin-top:2px">实际：${String(i.actual).slice(0,80)}</div>`:''}</td><td>${st(i)}</td><td>${i.fixable?'<span class="pill ok">可修复</span>':'<span class="pill mut">不可修复</span>'}</td></tr>`;
    }).join('')}</tbody></table>`;
  }
  // 耗时
  $('timing').innerHTML = `<table><colgroup><col style="width:25%"><col style="width:75%"></colgroup><thead><tr><th>环节</th><th class="num">耗时</th></tr></thead><tbody>
    <tr><td>解析（docx_to_json，Word → 结构化数据）</td><td class="num">${fmt(DATA.parse_ms,1)} ms</td></tr>
    <tr><td>检测（规则引擎）</td><td class="num">${fmt(DATA.detect_ms,1)} ms</td></tr>
    <tr><td>修复（/api/fix 写回）</td><td class="num">${fmt(DATA.fix_ms,1)} ms</td></tr>
  </tbody></table>
  <p class="note">真实文档没有人工金标准，不计算精确率/召回率，以上为统计结果。</p>`;
}
init();
</script>
</body>
</html>
"""


# ============ 汇总模板 ============
_INDEX_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>真实样张测试报告 · 汇总</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Crect width='24' height='24' rx='5' fill='%231F5E8E'/%3E%3Cpath d='M6 17V7h3v7h2V7h3v7h2V7h3v10z' fill='%23F6F4EF'/%3E%3C/svg%3E">
<link rel="stylesheet" href="https://miaoda.feishu.cn/fonts/css2?family=Noto+Sans+SC:wght@400;500;600;700&family=Noto+Serif+SC:wght@600;700;900&display=swap">
<script>/*__ECHARTS_INLINE__*/</script>
<style>
:root{
  --bg:#F6F4EF; --card:#FFFFFF; --ink:#17263A; --muted:#5A6B7C;
  --blue:#1F5E8E; --teal:#0E7C7B; --amber:#C47A1F; --red:#B3453A;
  --grid:#E5E1D8; --line:#D8D2C6; --chip:#EFECE4;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font:400 15px/1.65 "Noto Sans SC",system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto;padding:28px 20px 80px}
header.hero{border-bottom:1px solid var(--line);padding-bottom:24px;margin-bottom:28px}
.kicker{font-size:12px;letter-spacing:.18em;color:var(--blue);font-weight:600}
h1{font-family:"Noto Serif SC",serif;font-size:28px;line-height:1.3;margin:8px 0 6px;font-weight:700}
.sub{color:var(--muted);font-size:14px}
.grid{display:grid;gap:14px}
.cards4{grid-template-columns:repeat(4,1fr)}
@media(max-width:860px){.cards4{grid-template-columns:repeat(2,1fr)}}
@media(max-width:520px){.cards4{grid-template-columns:1fr}.wrap{padding:18px 12px 60px}h1{font-size:24px}}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;min-width:0}
.stat .v{font-size:28px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.2}
.stat .l{font-size:13px;color:var(--muted);margin-top:4px}
.stat .d{font-size:12px;color:var(--muted);margin-top:2px}
.stat.ok .v{color:var(--teal)} .stat.warn .v{color:var(--amber)} .stat.bad .v{color:var(--red)} .stat.blue .v{color:var(--blue)}
section{margin-top:40px}
h2{font-family:"Noto Serif SC",serif;font-size:21px;font-weight:700;margin:0 0 14px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin-top:14px}
.panel h3{font-size:15px;margin:0 0 12px;font-weight:600}
.chart{width:100%;height:340px}
table{width:100%;border-collapse:collapse;table-layout:fixed;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--grid);vertical-align:top;overflow-wrap:break-word}
th{color:var(--muted);font-weight:600;font-size:12px;letter-spacing:.02em;white-space:nowrap}
tr:last-child td{border-bottom:none}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap}
.pill.ok{background:#E2F0EE;color:var(--teal)} .pill.warn{background:#F7EBD9;color:var(--amber)} .pill.bad{background:#F5E2DF;color:var(--red)} .pill.mut{background:var(--chip);color:var(--muted)}
.note{font-size:12px;color:var(--muted);margin-top:8px}
.okline{font-size:13px;color:var(--teal);font-weight:600;margin:6px 0 0}
.mono{font-variant-numeric:tabular-nums;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
a.doc{color:var(--blue);text-decoration:none} a.doc:hover{text-decoration:underline}
</style>
</head>
<body>
<div class="wrap">

<header class="hero">
  <div class="kicker">真实样张测试 · 汇总报告</div>
  <h1>真实样张（学生论文）测试汇总</h1>
  <div class="sub" id="hero-sub"></div>
  <div class="grid cards4" id="cards" style="margin-top:22px"></div>
</header>

<section>
  <h2>逐文档结果</h2>
  <div class="panel"><div id="doc-table"></div>
    <p class="note">点击文档名可查看该篇的详细报告（单篇 HTML）。真实文档无人工金标准，只统计检出与修复结果。</p></div>
</section>

<section>
  <h2>问题分布（全部文档汇总）</h2>
  <div class="panel"><h3>按规则统计（规则编号 rule_id）</h3><div class="chart" id="chart-rule"></div>
    <p class="note" id="severity-note"></p></div>
</section>

</div>
<script>
const DATA = __RAW_DATA__;
const $ = (id) => document.getElementById(id);
const fmt = (v,d=3,def='—') => (v===null||v===undefined)?def:(typeof v==='number'?(Number.isInteger(v)?String(v):v.toFixed(d)):String(v));
function statCard(title, value, cls, desc){
  return `<div class="stat ${cls}"><div class="v">${value}</div><div class="l">${title}</div>${desc?`<div class="d">${desc}</div>`:''}</div>`;
}
const pill = (ok, okText='通过', badText='异常') => ok===true?`<span class="pill ok">${okText}</span>`:(ok===false?`<span class="pill bad">${badText}</span>`:'<span class="pill mut">—</span>');

function init(){
  const m = DATA.meta||{};
  const t = DATA.totals||{};
  $('hero-sub').textContent = `生成于 ${m.generated_at||'—'} · 学院 ${m.college_id||'—'} · 待测目录 ${DATA.samples_dir||'—'}`;
  $('cards').innerHTML =
    statCard('处理的文档数', fmt(t.documents_processed,0), 'blue', `含 ${fmt(t.documents_with_error,0)} 篇处理出错`) +
    statCard('检出问题总数', fmt(t.total_issues,0), (t.total_issues||0)>0?'warn':'ok', '全部文档检出问题条数之和') +
    statCard('平均每篇问题数', fmt(t.avg_issues_per_doc,1), 'blue', '检出总数 / 文档数') +
    statCard('修复应用率', fmt(DATA.fix_success_rate,3), (DATA.fix_success_rate||0)>=0.9?'ok':'warn', `已应用 ${fmt(t.applied,0)} / 提交 ${fmt(t.submitted,0)}`);
  const docs = DATA.documents||[];
  const tt=$('doc-table');
  if(!docs.length){
    tt.innerHTML = '<p class="okline">没有待测文档。</p>';
  } else {
    tt.innerHTML = `<table><colgroup><col style="width:22%"><col style="width:9%"><col style="width:9%"><col style="width:9%"><col style="width:8%"><col style="width:12%"><col style="width:12%"><col style="width:19%"></colgroup><thead><tr><th>文档</th><th class="num">检出</th><th class="num">可修复</th><th class="num">已应用</th><th class="num">回归</th><th>写回保真</th><th class="num">修复后剩余</th><th>处理</th></tr></thead><tbody>${docs.map(d=>{
      const stem = String(d.name).replace(/\.[^.]+$/,'') || '文档';
      const err = d.error?`<span class="pill bad">出错</span>`:'<span class="pill ok">成功</span>';
      return `<tr><td><a class="doc mono" href="${encodeURIComponent(stem)}.html">${d.name}</a></td>
        <td class="num">${fmt(d.issue_count,0)}</td>
        <td class="num">${fmt(d.submitted,0)}</td>
        <td class="num">${fmt(d.applied,0)}</td>
        <td class="num">${fmt(d.regressions,0)}</td>
        <td>${pill(d.openable,'可打开','损坏')} ${pill(d.parts_preserved,'一致','有变更')}</td>
        <td class="num">${d.remaining_after_fix==null?'—':fmt(d.remaining_after_fix,0)}</td>
        <td>${err}</td></tr>`;
    }).join('')}</tbody></table>`;
  }
  const byRule = DATA.by_rule||{};
  const names = Object.keys(byRule).sort((a,b)=>byRule[b].count-byRule[a].count);
  const el=$('chart-rule');
  if(window.echarts && names.length){
    const chart=echarts.init(el);
    chart.setOption({grid:{left:190,right:50,top:20,bottom:40},
      tooltip:{trigger:'axis',axisPointer:{type:'shadow'},confine:true},
      xAxis:{type:'value',minInterval:1,name:'条数'},
      yAxis:{type:'category',data:names,axisLabel:{fontSize:11,fontFamily:'ui-monospace,Consolas,monospace'}},
      series:[{type:'bar',data:names.map(n=>byRule[n].count),itemStyle:{color:'#1F5E8E'},barMaxWidth:26,label:{show:true,position:'right'}}]});
    window.addEventListener('resize',()=>chart.resize());
  } else el.textContent='无检出问题数据';
  const sev = DATA.by_severity||{};
  $('severity-note').textContent = `按严重级别：${Object.keys(sev).map(k=>`${k} ${sev[k]} 条`).join('，')||'无'}。`;
}
init();
</script>
</body>
</html>
"""
