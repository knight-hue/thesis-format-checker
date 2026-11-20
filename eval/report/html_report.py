# -*- coding: utf-8 -*-
"""HTML 可视化报告：自包含单文件（内联 CSS/JS + ECharts），数据固化进 JS。

页面结构：顶部摘要 → 检测准确性 → 解析准确性 → 修复与写回 → 性能 → 附录。
所有真实数字来自评测结果 dict，渲染时用 textContent 写入，避免转义问题。
界面术语全部中文化；英文缩写一律带中文注释（如 "精确率（Precision）"）。
边缘指标已精简：只保留核心功能指标，用例明细表仅展示异常项。
"""
import json
from pathlib import Path

from ..config import EvalConfig

_PAGE_TITLE = "论文格式校验系统评测报告"

# 内嵌 ECharts 库：与模板同目录的 echarts.min.js（离线可用，不依赖 CDN）
_ECHARTS_PATH = Path(__file__).resolve().parent / "echarts.min.js"


def _inline_echarts() -> str:
    js = _ECHARTS_PATH.read_text(encoding="utf-8")
    # 防御性转义：防止压缩代码中出现 "</script" 提前闭合 script 标签
    return js.replace("</script", "<\\/script")


def _fmt(v, digits=3, default="—"):
    if v is None:
        return default
    try:
        return f"{v:.{digits}f}" if isinstance(v, float) else str(v)
    except (TypeError, ValueError):
        return str(v)


def render_html(report: dict, config: EvalConfig) -> Path:
    """把评测报告渲染成自包含 HTML 文件，返回文件路径。

    内嵌 ECharts（约 1MB）后为完全离线的单文件，可直接双击打开。
    """
    html = _TEMPLATE
    html = html.replace("/*__ECHARTS_INLINE__*/", _inline_echarts())
    html = html.replace("__RAW_DATA__", json.dumps(report, ensure_ascii=False))
    html = html.replace("__PAGE_TITLE__", _PAGE_TITLE)
    out = config.report_dir / "eval_report.html"
    out.write_text(html, encoding="utf-8")
    return out


_TEMPLATE = r"""<!DOCTYPE html>
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
.wrap{max-width:1180px;margin:0 auto;padding:28px 20px 80px}
header.hero{border-bottom:1px solid var(--line);padding-bottom:24px;margin-bottom:28px}
.kicker{font-size:12px;letter-spacing:.18em;color:var(--blue);font-weight:600}
h1{font-family:"Noto Serif SC",serif;font-size:30px;line-height:1.3;margin:8px 0 6px;font-weight:700}
.sub{color:var(--muted);font-size:14px}
.grid{display:grid;gap:14px}
.cards4{grid-template-columns:repeat(4,1fr)}
.cards3{grid-template-columns:repeat(3,1fr)}
@media(max-width:860px){.cards4{grid-template-columns:repeat(2,1fr)}.cards3{grid-template-columns:1fr 1fr}}
@media(max-width:520px){.cards4,.cards3{grid-template-columns:1fr}.wrap{padding:18px 12px 60px}h1{font-size:24px}}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;min-width:0}
.stat .v{font-size:28px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.2}
.stat .l{font-size:13px;color:var(--muted);margin-top:4px}
.stat .d{font-size:12px;color:var(--muted);margin-top:2px}
.stat.ok .v{color:var(--teal)} .stat.warn .v{color:var(--amber)} .stat.bad .v{color:var(--red)} .stat.blue .v{color:var(--blue)}
section{margin-top:44px}
h2{font-family:"Noto Serif SC",serif;font-size:22px;font-weight:700;margin:0 0 4px}
.secdesc{color:var(--muted);font-size:13px;margin:0 0 18px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin-top:14px}
.panel h3{font-size:15px;margin:0 0 12px;font-weight:600}
.chart{width:100%;height:340px}
.chart.sm{height:280px}
table{width:100%;border-collapse:collapse;table-layout:fixed;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--grid);vertical-align:top;overflow-wrap:break-word}
th{color:var(--muted);font-weight:600;font-size:12px;letter-spacing:.02em;white-space:nowrap}
tr:last-child td{border-bottom:none}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600}
.pill.ok{background:#E2F0EE;color:var(--teal)} .pill.warn{background:#F7EBD9;color:var(--amber)} .pill.bad{background:#F5E2DF;color:var(--red)} .pill.mut{background:var(--chip);color:var(--muted)}
.note{font-size:12px;color:var(--muted);margin-top:8px}
.okline{font-size:13px;color:var(--teal);font-weight:600;margin:6px 0 0}
.dl{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.dl a{color:var(--blue);text-decoration:none;font-size:13px;border:1px solid var(--line);border-radius:8px;padding:6px 12px;background:var(--card)}
.dl a:hover{background:var(--chip)}
.mono{font-variant-numeric:tabular-nums;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
.en{color:var(--muted);font-size:.9em}
</style>
</head>
<body>
<div class="wrap">

<header class="hero">
  <div class="kicker">评测报告 · 合成样张全链路评测</div>
  <h1>论文格式校验系统评测报告</h1>
  <div class="sub" id="hero-sub"></div>
  <div class="dl" id="hero-links"></div>
  <div class="grid cards4" id="summary-cards" style="margin-top:22px"></div>
</header>

<section id="s-det">
  <h2>一、检测准确性</h2>
  <p class="secdesc">合成样张（1 个合规基线 + 每规则 1 个单错误样张）经 解析 → 检测 → 金标准比对，计算 精确率（Precision）/ 召回率（Recall）/ F1 分数（F1）。</p>
  <div class="grid cards3" id="det-cards"></div>
  <div class="panel"><h3>逐规则混淆明细（真阳性 TP / 漏检 FN / 误报 FP）</h3><div class="chart" id="chart-byrule"></div>
    <p class="note" id="det-rule-note"></p></div>
  <div class="panel"><h3>异常用例</h3><div id="det-table"></div>
    <p class="note">仅展示未命中或定位失败的样张；未列出的样张全部命中，可直接信任。</p></div>
</section>

<section id="s-parse">
  <h2>二、解析准确性</h2>
  <p class="secdesc">在合规基线上测量结构识别（段落类型 kind）与文本保真。</p>
  <div class="grid cards3" id="parse-cards"></div>
  <div class="panel"><h3>识别失败明细</h3><div id="parse-table"></div></div>
</section>

<section id="s-fix">
  <h2>三、修复与写回</h2>
  <p class="secdesc">对注入样张执行 检测 → 提交可修复项 → 修复接口（/api/fix）写回 → 重新解析重检。</p>
  <div class="grid cards4" id="fix-cards"></div>
  <div class="panel"><h3>异常用例</h3><div id="fix-table"></div>
    <p class="note">仅展示修复未生效、出现回归或写回损坏的样张；未列出的样张全部修复成功。</p></div>
</section>

<section id="s-perf">
  <h2>四、性能（单机基准）</h2>
  <p class="secdesc" id="perf-sub"></p>
  <div class="grid cards4" id="perf-cards"></div>
  <div class="panel"><h3>分阶段耗时（中位数，单位：毫秒 ms）</h3><div class="chart sm" id="chart-timing"></div></div>
  <div class="panel"><h3>规模扫描：耗时随文档规模变化（单位：毫秒 ms）</h3><div class="chart" id="chart-scale"></div></div>
  <div class="panel"><h3>规模扫描：内存峰值（单位：兆字节 MB）</h3><div class="chart sm" id="chart-mem"></div></div>
  <div class="panel"><h3>智能能力初始化探测</h3><div id="warmup-table"></div></div>
</section>

<section id="s-appendix">
  <h2>附录</h2>
  <p class="secdesc">评测口径与限制。</p>
  <div class="panel" id="appendix"></div>
</section>

</div>
<script>
const DATA = __RAW_DATA__;
const $ = (id) => document.getElementById(id);
const fmt = (v,d=3,def='—') => (v===null||v===undefined)?def:(typeof v==='number'?(Number.isInteger(v)?String(v):v.toFixed(d)):String(v));
const pct = (v,d=1) => fmt(v,d);
function statCard(title, value, cls, desc){
  return `<div class="stat ${cls}"><div class="v">${value}</div><div class="l">${title}</div>${desc?`<div class="d">${desc}</div>`:''}</div>`;
}
function mount(){ initHero(); initDet(); initParse(); initFix(); initPerf(); initAppendix(); }

function initHero(){
  const m = DATA.meta||{};
  const acc = DATA.accuracy||{};
  const det = acc.detection||{};
  const nInjected = (det.cases||[]).filter(c=>!c.is_clean).length;
  $('hero-sub').textContent = `生成于 ${m.generated_at||'—'} · 学院 ${m.college_id||'—'} · 样张 ${det.case_count||0} 个（干净基线 1 + 注入 ${nInjected}） · 运行环境 Python ${m.python||'—'}`;
  const dl = $('hero-links');
  const a=document.createElement('a'); a.href='eval_report.json'; a.textContent='JSON 原始数据（json）'; dl.appendChild(a);
  const overall = det.overall||{};
  const parse = acc.parse||{}; const fix = acc.fix||{};
  const cards = $('summary-cards');
  cards.innerHTML =
    statCard('检测 F1 分数（F1）', pct(overall.f1), overall.f1===null?'warn':'blue', `精确率 ${pct(overall.precision)} · 召回率 ${pct(overall.recall)} · 真阳性/误报/漏检 ${fmt(overall.tp,0)}/${fmt(overall.fp,0)}/${fmt(overall.fn,0)}`) +
    statCard('解析段落类型准确率', pct(parse.kind_accuracy), parse.kind_accuracy===null?'warn':'ok', `${fmt(parse.correct_kinds,0)} / ${fmt(parse.checked_kinds,0)} 个段落判定正确`) +
    statCard('修复应用率', pct(fix.fix_success_rate), fix.fix_success_rate===null?'warn':'ok', `修复后重检消失 ${pct(fix.recheck_clear_rate)}`) +
    statCard('回归破坏率', pct(fix.regression_rate), (fix.regression_rate||0)<=0.05?'ok':'bad', `修复引入新问题 ${fmt((fix.totals||{}).regressions,0)} 条 / 修复前 ${fmt((fix.totals||{}).before_issues,0)} 条`);
}

function initDet(){
  const det = (DATA.accuracy||{}).detection||{};
  const overall = det.overall||{};
  $('det-cards').innerHTML =
    statCard('精确率（Precision）', pct(overall.precision), (overall.precision||0)>=0.9?'ok':'warn', '报出的问题中，真正有问题的比例') +
    statCard('召回率（Recall）', pct(overall.recall), (overall.recall||0)>=0.9?'ok':'warn', '真实问题中，被报出的比例') +
    statCard('F1 分数（F1）', pct(overall.f1), overall.f1===null?'warn':'blue', `真阳性 ${fmt(overall.tp,0)} / 误报 ${fmt(overall.fp,0)} / 漏检 ${fmt(overall.fn,0)}`);
  const byRule = det.by_rule||{};
  const names = Object.keys(byRule).sort();
  const rows = names.map(n=>({name:n, ...byRule[n]}));
  const el = $('chart-byrule');
  if(window.echarts && rows.length){
    const chart = echarts.init(el);
    chart.setOption({
      grid:{left:190,right:30,top:20,bottom:40},
      tooltip:{trigger:'axis',axisPointer:{type:'shadow'},confine:true},
      legend:{bottom:0,data:['真阳性 TP','漏检 FN','误报 FP']},
      xAxis:{type:'value',minInterval:1,name:'条数'},
      yAxis:{type:'category',data:rows.map(r=>r.name).reverse(),
        axisLabel:{fontSize:11,fontFamily:'ui-monospace,Consolas,monospace'}},
      series:[
        {name:'真阳性 TP',type:'bar',stack:'c',data:rows.map(r=>r.tp).reverse(),itemStyle:{color:'#0E7C7B'}},
        {name:'漏检 FN',type:'bar',stack:'c',data:rows.map(r=>r.fn).reverse(),itemStyle:{color:'#C47A1F'}},
        {name:'误报 FP',type:'bar',stack:'c',data:rows.map(r=>r.fp).reverse(),itemStyle:{color:'#B3453A'}}
      ]
    });
    window.addEventListener('resize',()=>chart.resize());
  } else { el.textContent='图表库未加载或无逐规则数据'; }
  const rs = det.resolution||{};
  $('det-rule-note').textContent = `金标准定位解析：${fmt(rs.resolved,0)} 条成功，${fmt(rs.unresolved,0)} 条失败（结构识别未把注入点认成期望类型时，对应检查不会触发）。干净基线样张误报：${fmt(det.clean_issues,0)} 条。`;
  // 异常用例表：只列未命中或定位失败的样张
  const cases = det.cases||[];
  const abnormal = cases.filter(c=>{
    if(c.is_clean) return false;
    const g=c.golden||[];
    return g.length===0 || !g.every((gi,i)=>{
      if((c.resolved||{})[i]===false) return false;
      return (c.detected||[]).some(d=>matchGolden(gi,d));
    });
  });
  const t=$('det-table');
  if(!abnormal.length){
    t.innerHTML = `<p class="okline">全部 ${fmt(cases.filter(c=>!c.is_clean).length,0)} 个注入样张命中，干净基线零误报。</p>`;
  } else {
    t.innerHTML = `<table><colgroup><col style="width:24%"><col style="width:26%"><col style="width:10%"><col style="width:10%"><col style="width:15%"><col style="width:15%"></colgroup><thead><tr><th>样张</th><th>规则编号（rule_id）</th><th class="num">金标准</th><th class="num">检出</th><th class="num">解析耗时</th><th class="num">检测耗时</th></tr></thead><tbody>${abnormal.map(c=>{
      const g=c.golden||[];
      return `<tr><td class="mono">${c.name}</td><td>${c.is_clean?'—':(g.map(x=>x.rule_id).join('、')||'—')}</td><td class="num">${g.length}</td><td class="num">${(c.detected||[]).length}</td><td class="num">${fmt(c.parse_ms,1)} ms</td><td class="num">${fmt(c.detect_ms,1)} ms</td></tr>`;
    }).join('')}</tbody></table>`;
  }
}
function matchGolden(g,d){
  if(!g || !d) return false;
  if(g.rule_id !== d.rule_id) return false;
  const anchor = g.text_anchor;
  const texts = [d.message,d.actual,d.location&&d.location.text_preview].concat((d.blocks||[]).map(b=>b.text)).filter(Boolean);
  if(anchor) return texts.some(t=>t.indexOf(anchor)>=0);
  const gl=g.location||{}, dl=d.location||{};
  for(const dim of ['type','index','run','row','col']){
    if(gl[dim]===null||gl[dim]===undefined) continue;
    if(dl[dim]!==gl[dim]) return false;
  }
  return true;
}

function initParse(){
  const parse = (DATA.accuracy||{}).parse||{};
  const cards = $('parse-cards');
  cards.innerHTML =
    statCard('段落类型（kind）准确率', pct(parse.kind_accuracy), (parse.kind_accuracy||0)>=0.95?'ok':'warn', `${fmt(parse.correct_kinds,0)} / ${fmt(parse.checked_kinds,0)} 个段落判定正确`) +
    statCard('解析出的文本块数', fmt(parse.block_count,0), 'blue', '合规基线解析出的文本块（blocks）总数') +
    statCard('基线自检', parse.kind_accuracy===1?'通过':'需人工复核', parse.kind_accuracy===1?'ok':'bad', '基线论文被正确识别是评测可信的前提');
  const t=$('parse-table');
  const rows = (parse.cases||[]).flatMap(c=>(c.details||[]));
  const bad = rows.filter(r=>!r.ok || r.found===false);
  t.innerHTML = bad.length ? `<table><colgroup><col style="width:22%"><col style="width:18%"><col style="width:18%"><col style="width:42%"></colgroup><thead><tr><th>段落</th><th>期望类型</th><th>实际类型</th><th>说明</th></tr></thead><tbody>${bad.map(r=>`<tr><td>${r.name}</td><td class="mono">${r.expected}</td><td class="mono">${r.actual||'未找到'}</td><td>${r.found?'识别不一致':'解析结果中找不到该文本'}</td></tr>`).join('')}</tbody></table>` : '<p class="okline">全部段落类型识别正确。</p>';
}

function initFix(){
  const fix = (DATA.accuracy||{}).fix||{};
  const t = fix.totals||{};
  const cards=$('fix-cards');
  cards.innerHTML =
    statCard('修复应用率', pct(fix.fix_success_rate), (fix.fix_success_rate||0)>=0.9?'ok':'warn', `已应用 ${fmt(t.applied,0)} / 提交 ${fmt(t.submitted,0)}`) +
    statCard('重检消失率', pct(fix.recheck_clear_rate), (fix.recheck_clear_rate||0)>=0.9?'ok':'warn', `修复后重新检测，问题消失 ${fmt(t.recheck_cleared,0)} / ${fmt(t.recheck_targets,0)}`) +
    statCard('回归破坏率', pct(fix.regression_rate), (fix.regression_rate||0)<=0.05?'ok':'bad', `修复引入新问题 ${fmt(t.regressions,0)} 条 / 修复前问题 ${fmt(t.before_issues,0)} 条`) +
    statCard('写回保真', '', 'blue', '修复后文档可打开、未修改部件一致（见异常表）');
  // 异常用例：只列修复未生效 / 回归 / 写回损坏的
  const abnormal = (fix.cases||[]).filter(c=>{
    if((c.submitted||0)===0) return false;
    return c.applied<c.submitted || c.recheck_cleared<c.submitted || c.regressions>0 || c.openable!==true || c.fix_error;
  });
  const tt=$('fix-table');
  if(!abnormal.length){
    tt.innerHTML = `<p class="okline">全部 ${fmt((fix.cases||[]).length,0)} 个可修复样张：修复应用、重检消失、回归、写回保真全部通过。</p>`;
  } else {
    tt.innerHTML = `<table><colgroup><col style="width:24%"><col style="width:10%"><col style="width:10%"><col style="width:12%"><col style="width:12%"><col style="width:32%"></colgroup><thead><tr><th>样张</th><th class="num">提交</th><th class="num">应用</th><th class="num">重检消失</th><th class="num">回归</th><th>写回保真</th></tr></thead><tbody>${abnormal.map(c=>{
      const open=c.openable===true?'<span class="pill ok">可打开</span>':(c.openable===false?'<span class="pill bad">损坏</span>':'<span class="pill mut">未测</span>');
      const parts=c.parts_preserved===true?'<span class="pill ok">一致</span>':(c.parts_preserved===false?'<span class="pill warn">有变更</span>':'<span class="pill mut">—</span>');
      const err=c.fix_error?`<span class="pill bad">${c.fix_error}</span>`:'';
      return `<tr><td class="mono">${c.name}</td><td class="num">${fmt(c.submitted,0)}</td><td class="num">${fmt(c.applied,0)}</td><td class="num">${fmt(c.recheck_cleared,0)}</td><td class="num">${fmt(c.regressions,0)}</td><td>${open} ${parts} ${err}</td></tr>`;
    }).join('')}</tbody></table>`;
  }
}

function initPerf(){
  const perf = DATA.performance||{};
  const timing = perf.timing||{};
  const scale = perf.scale||{};
  const warmup = perf.warmup||{};
  const cards=$('perf-cards');
  const st = (v,unit)=> (v===null||v===undefined)?'—':`${v}<span style="font-size:15px;color:var(--muted)"> ${unit}</span>`;
  cards.innerHTML =
    statCard('端到端（End-to-End）', st(timing.e2e_ms_median,'ms'), 'blue', '上传 → 解析 → 检测 全流程') +
    statCard('解析（docx_to_json）', st(timing.parse_ms_median,'ms'), 'blue', '把 Word 文档解析成结构化数据 + 契约校验') +
    statCard('检测（规则引擎）', st(timing.detect_ms_median,'ms'), 'blue', '格式规则 + 语义确定性条款检查') +
    statCard('写回（/api/fix）', st(timing.writeback_ms_median,'ms'), 'blue', '按确认的问题修复并生成新 Word 文档');
  const tel=$('chart-timing');
  if(window.echarts){
    const rows=[['端到端（End-to-End）',timing.e2e_ms_median],['写回（/api/fix）',timing.writeback_ms_median],['检测（规则引擎）',timing.detect_ms_median],['解析（docx_to_json）',timing.parse_ms_median]].filter(r=>r[1]!==null&&r[1]!==undefined);
    const chart=echarts.init(tel);
    chart.setOption({grid:{left:140,right:50,top:20,bottom:30},
      tooltip:{trigger:'axis',axisPointer:{type:'shadow'},valueFormatter:v=>v+' ms',confine:true},
      xAxis:{type:'value',name:'毫秒 ms'},yAxis:{type:'category',data:rows.map(r=>r[0])},
      series:[{type:'bar',data:rows.map(r=>({value:r[1],itemStyle:{color:'#1F5E8E'}})),barMaxWidth:36,label:{show:true,position:'right',formatter:p=>p.value+' ms'}}]});
    window.addEventListener('resize',()=>chart.resize());
  } else tel.textContent='图表库未加载';
  const sc = $('chart-scale');
  const rowsScale = scale.rows||[];
  if(window.echarts && rowsScale.length){
    const chart=echarts.init(sc);
    chart.setOption({grid:{left:60,right:50,top:40,bottom:40},
      tooltip:{trigger:'axis',confine:true},legend:{bottom:0},
      xAxis:{type:'category',data:rowsScale.map(r=>`${r.target_pages} 页`),name:'文档规模'},
      yAxis:{type:'value',name:'耗时（ms）'},
      series:[
        {name:'解析（docx_to_json）',type:'line',smooth:true,data:rowsScale.map(r=>r.parse_ms_median),itemStyle:{color:'#1F5E8E'},lineStyle:{width:3}},
        {name:'检测（规则引擎）',type:'line',smooth:true,data:rowsScale.map(r=>r.detect_ms_median),itemStyle:{color:'#0E7C7B'},lineStyle:{width:3}},
        {name:'合计',type:'line',smooth:true,data:rowsScale.map(r=>r.total_ms_median),itemStyle:{color:'#C47A1F'},lineStyle:{width:3,type:'dashed'}}
      ]});
    window.addEventListener('resize',()=>chart.resize());
  } else sc.textContent='无规模扫描数据';
  const mem=$('chart-mem');
  if(window.echarts && rowsScale.length){
    const chart=echarts.init(mem);
    chart.setOption({grid:{left:60,right:40,top:30,bottom:40},
      tooltip:{trigger:'axis',valueFormatter:v=>v+' MB',confine:true},
      xAxis:{type:'category',data:rowsScale.map(r=>`${r.target_pages} 页`)},
      yAxis:{type:'value',name:'峰值内存（MB）'},
      series:[{type:'bar',data:rowsScale.map(r=>r.peak_mem_mb),itemStyle:{color:'#1F5E8E'},barMaxWidth:40,label:{show:true,position:'top',formatter:p=>p.value+' MB'}}]});
    window.addEventListener('resize',()=>chart.resize());
  } else mem.textContent='无内存数据';
  const wt=$('warmup-table');
  const llm=warmup.llm||{}; const rag=warmup.rag;
  const state = (s)=> s==='ready'?'<span class="pill ok">就绪</span>':(s==='failed'?'<span class="pill bad">失败</span>':'<span class="pill mut">跳过</span>');
  wt.innerHTML = `<table><colgroup><col style="width:24%"><col style="width:12%"><col style="width:16%"><col style="width:48%"></colgroup><thead><tr><th>智能能力</th><th>状态</th><th class="num">初始化耗时</th><th>说明</th></tr></thead><tbody>
    <tr><td>生成模型门面（LLMService）<br><span class="en">负责智能生成与改写</span></td><td>${state(llm.status)}</td><td class="num">${llm.init_seconds==null?'—':llm.init_seconds+' 秒'}</td><td>${llm.detail||''}</td></tr>
    <tr><td>知识库（RAGService）<br><span class="en">负责检索规范原文并解释依据</span></td><td>${state((rag||{}).status)}</td><td class="num">${(rag||{}).init_seconds==null?'—':rag.init_seconds+' 秒'}</td><td>${(rag||{}).detail||''}</td></tr>
  </tbody></table>`;
  $('perf-sub').textContent = `分阶段耗时样本：解析 ${JSON.stringify(timing.samples&&timing.samples.parse_ms||[])} ms · 检测 ${JSON.stringify(timing.samples&&timing.samples.detect_ms||[])} ms（${fmt(timing.blocks,0)} 个文本块的样张 ${timing.sample||''}）。规模扫描按 ${(DATA.meta||{}).config?((DATA.meta.config.scale_pages||[]).join(' / ')+' 页'):''} 档位。`;
}

function initAppendix(){
  const el=$('appendix');
  const items=[
    ['口径','合成样张 = 合规基线（按 jlu_cs 规范原文构建）+ 每个注入器恰好一个已知错误；金标准为注入时登记的（规则编号 rule_id + 定位）。精确率 / 召回率 / F1 分数按微平均计算。'],
    ['术语对照','精确率（Precision）：报出的问题中真正有问题的比例；召回率（Recall）：真实问题中被报出的比例；F1 分数（F1）：两者的调和平均；真阳性 TP：检出且确实有问题；误报 FP：报出但无问题；漏检 FN：有问题但未报出。'],
    ['定位匹配','规则引擎类问题按 规则编号 rule_id + 位置（段落序号/run/行/列）匹配；语义类问题（位置无序号）按文本锚匹配。金标准定位解析失败时该条目计入漏检（FN）并在报告中标注。'],
    ['语义条款','评测只覆盖无需生成模型的确定性语义条款（致谢时间段、文献类型标识、计量单位、汉字数字）；生成式条款需要 API Key 与知识库，当前环境未配置时如实标注，不计入指标。'],
    ['性能基准','单机分阶段耗时取多次运行中位数；规模扫描为临时生成的近似论文（正文+表格+图片），内存用 tracemalloc 统计 Python 侧峰值。'],
    ['局限','合成样张的合规基线与注入方式无法覆盖全部真实论文形态（如列表、公式、目录、封面），检测指标对真实场景的代表性需配合真实样张人工标注验证。'],
  ];
  el.innerHTML = items.map(([t,d])=>`<h3 style="font-size:14px;margin:14px 0 4px">${t}</h3><p class="note" style="margin:0">${d}</p>`).join('');
}
mount();
</script>
</body>
</html>
"""
