# -*- coding: utf-8 -*-
"""评测总入口：合成样张（准确性+性能）与 真实样张（自由导入文档）分目录出报告。

报告结构（eval/out/ 下两个分类目录，各按时间戳留档）：
    eval/out/synthetic/test_<时间戳>/eval_report.html + eval_report.json
        合成样张全链路报告：检测准确性 / 解析准确性 / 修复与写回 / 性能基准
    eval/out/real/test_<时间戳>/index.html + 每篇文档 <文档名>.html + 对应 .json
        真实样张测试报告：汇总页 + 每篇文档独立报告（检出分布 / 修复 / 写回 / 耗时）

用法（项目根目录，.venv 内）：
    python -m eval.run                    # 跑全部（合成 + 真实样张 + 性能）
    python -m eval.run --stage accuracy   # 只跑合成准确性
    python -m eval.run --stage real       # 只跑真实样张（自由导入的 Word 文档）
    python -m eval.run --stage perf       # 只跑性能
    python -m eval.run --keep-artifacts   # 保留合成样张 docx 便于人工复查
    python -m eval.run --samples-dir <路径>  # 指定真实样张目录（默认 eval/samples）
"""
import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eval.config import EvalConfig
from eval.report.html_report import render_html
from eval.report.json_report import build_report_meta, dump_json
from eval.report.real_report import render_real_reports
from eval.synthetic.corpus import build_corpus, clear_data


def parse_args():
    parser = argparse.ArgumentParser(description="论文格式校验系统评测")
    parser.add_argument("--stage", default="all",
                        choices=["all", "accuracy", "real", "perf"],
                        help="评测范围（默认 all）")
    parser.add_argument("--college", default="jlu_cs", help="学院标识（默认 jlu_cs）")
    parser.add_argument("--keep-artifacts", action="store_true",
                        help="保留合成样张 docx（默认清理）")
    parser.add_argument("--no-clean", action="store_true",
                        help="不清理上次评测数据（默认清理）")
    parser.add_argument("--report-dir", default=None,
                        help="合成报告输出目录（默认 eval/out/synthetic；真实报告在其 real/ 子目录）")
    parser.add_argument("--samples-dir", default=None,
                        help="真实样张目录（默认 eval/samples，放学生论文 Word 文档）")
    return parser.parse_args()


def _log(msg):
    print(f"[eval] {msg}", flush=True)


def run(synth_cfg: EvalConfig, real_cfg: EvalConfig, stage: str):
    """合成与真实样张分别出报告。返回 (report_synth, report_real)。"""
    report_synth = {"meta": build_report_meta(synth_cfg)}
    report_real = {"meta": build_report_meta(real_cfg)}
    t_all = time.perf_counter()

    if stage in ("all", "accuracy"):
        _log("构建合成样张语料…")
        cases = build_corpus(synth_cfg)
        _log(f"共 {len(cases)} 个样张（含 {sum(1 for c in cases if c.is_clean)} 个干净基线）")

        from eval.runner.detect import run_detection_eval
        _log("检测层评测…")
        detection = run_detection_eval(cases, synth_cfg)
        report_synth.setdefault("accuracy", {})["detection"] = detection
        o = detection["overall"]
        _log(f"  检测 P={o['precision']} R={o['recall']} F1={o['f1']} "
             f"(TP {o['tp']} / FP {o['fp']} / FN {o['fn']})")

        from eval.runner.parse import run_parse_eval
        _log("解析层评测…")
        report_synth["accuracy"]["parse"] = run_parse_eval(cases, synth_cfg)

        from eval.runner.fix import run_fix_eval
        _log("修复层评测…")
        fix = run_fix_eval(cases, synth_cfg)
        report_synth["accuracy"]["fix"] = fix
        _log(f"  修复应用率={fix['fix_success_rate']} "
             f"重检消失率={fix['recheck_clear_rate']} "
             f"回归破坏率={fix['regression_rate']}")

    if stage in ("all", "real"):
        from eval.runner.real import run_real_eval
        _log(f"真实样张评测（目录：{real_cfg.samples_dir}）…")
        report_real["real"] = run_real_eval(real_cfg)
        real = report_real["real"]
        if real.get("skipped"):
            _log(f"  真实样张跳过：{real.get('reason')}")
        else:
            t = real["totals"]
            _log(f"  文档 {t['documents_processed']} 篇，检出问题 {t['total_issues']} 条"
                 f"，修复应用率={real.get('fix_success_rate')}")
            files = render_real_reports(report_real, real_cfg)
            _log(f"  真实样张报告：{real_cfg.report_dir}（{len(files)} 个文件，含每篇 HTML）")

    if stage in ("all", "perf"):
        cases = None
        if "accuracy" not in report_synth or "detection" not in report_synth.get("accuracy", {}):
            _log("构建样张用于性能基准…")
            cases = build_corpus(synth_cfg)
        else:
            from eval.synthetic.corpus import _build_docx
            cases = [_build_docx(synth_cfg, "perf_sample", mutate=None)]

        from eval.perf.scale import run_scale_eval
        from eval.perf.timing import run_timing_eval, run_warmup_probe
        _log("分阶段耗时基准…")
        report_synth.setdefault("performance", {})["timing"] = run_timing_eval(cases, synth_cfg)
        _log("规模扫描…")
        report_synth["performance"]["scale"] = run_scale_eval(synth_cfg)
        _log("预热探测…")
        report_synth["performance"]["warmup"] = run_warmup_probe(synth_cfg)

    if report_synth.get("accuracy") or report_synth.get("performance"):
        json_path = dump_json(report_synth, synth_cfg)
        html_path = render_html(report_synth, synth_cfg)
        _log(f"合成样张报告已生成：\n  JSON {json_path}\n  HTML {html_path}")
    _log(f"总耗时 {time.perf_counter() - t_all:.1f}s")
    return report_synth, report_real


def main():
    args = parse_args()
    if args.report_dir:
        base = Path(args.report_dir)
        synth_cfg = EvalConfig(
            college_id=args.college,
            keep_artifacts=args.keep_artifacts,
            report_dir=base,
        )
        real_cfg = EvalConfig(
            college_id=args.college,
            keep_artifacts=args.keep_artifacts,
            report_dir=base / "real",
        )
    else:
        # 留档：两类报告分别输出到 synthetic/ 与 real/ 下的 test_<时间戳>/，互不覆盖
        ts = time.strftime("%Y%m%d_%H%M%S")
        synth_cfg = EvalConfig(
            college_id=args.college,
            keep_artifacts=args.keep_artifacts,
            report_dir=PROJECT_ROOT / "eval" / "out" / "synthetic" / f"test_{ts}",
        )
        real_cfg = EvalConfig(
            college_id=args.college,
            keep_artifacts=args.keep_artifacts,
            report_dir=PROJECT_ROOT / "eval" / "out" / "real" / f"test_{ts}",
        )
    if args.samples_dir:
        real_cfg.samples_dir = Path(args.samples_dir)
    if not args.no_clean:
        clear_data(synth_cfg)
    synth_cfg.ensure_dirs()
    # real_cfg 目录不在此处预创建：若 samples 目录没有 .docx，真实样张阶段会
    # 被跳过（skipped），此时不应留下空壳目录；真正有文档要跑时，
    # render_real_reports() 内部会创建 report_dir。
    report_synth, report_real = run(synth_cfg, real_cfg, args.stage)
    # 打印简短摘要
    det = report_synth.get("accuracy", {}).get("detection", {})
    if det:
        o = det.get("overall", {})
        print("\n===== 评测摘要 =====")
        print(f"合成 检测 P={o.get('precision')}  R={o.get('recall')}  F1={o.get('f1')}")
        rs = det.get("resolution", {})
        print(f"金标准定位解析失败 {rs.get('unresolved', 0)} 条（对应 FN）")
        fix = report_synth.get("accuracy", {}).get("fix", {})
        if fix:
            print(f"修复  应用率={fix.get('fix_success_rate')}  "
                  f"重检消失率={fix.get('recheck_clear_rate')}  "
                  f"回归率={fix.get('regression_rate')}")
        perf = report_synth.get("performance", {})
        if perf:
            t = perf.get("timing", {})
            print(f"性能  端到端={t.get('e2e_ms_median')}ms  "
                  f"解析={t.get('parse_ms_median')}ms  "
                  f"检测={t.get('detect_ms_median')}ms")
    real = report_real.get("real", {})
    if real and not real.get("skipped"):
        rt = real.get("totals", {})
        print(f"真实样张  文档 {rt.get('documents_processed')} 篇  "
              f"检出 {rt.get('total_issues')} 条  "
              f"修复应用率={real.get('fix_success_rate')}  "
              f"报告目录 {real_cfg.report_dir}")


if __name__ == "__main__":
    main()
