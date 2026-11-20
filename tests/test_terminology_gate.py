# -*- coding: utf-8 -*-
"""运行时术语 gate（P1-2a）的确定性单元测试。

不接 LLM、不接 FastAPI，只验证：
- 候选过滤与四范围首次出现；
- 括号释义必须真正展开；
- AI 条件豁免 / 严格模式；
- B04 默认关闭、开启后仍需论文实际领域为医学；
- 无逐段解析（PDF）→ context_unavailable。
"""
import unittest

from backend.detection.semantic.terminology import (
    REASON_CONTEXT,
    REASON_EXEMPT,
    REASON_NO_GLOSS,
    REASON_NOT_FIRST,
    evaluate_document,
    evaluate_token,
    load_policy,
)


def doc(*blocks):
    return {
        "blocks": [
            {"type": "paragraph", "index": index, "kind": kind, "text": text}
            for index, (kind, text) in enumerate(blocks)
        ]
    }


def by_token(decisions, token):
    return [item for item in decisions
            if item.occurrence.token.upper() == token.upper()]


def medical_doc(body="ECG信号用于诊断。"):
    return doc(
        ("title", "基于心电信号的疾病诊断"),
        ("keyword_body", "心电；临床；诊断"),
        ("abstract_body", "本文研究心电、临床诊断与患者数据。"),
        ("body", body),
    )


class PolicyDataTest(unittest.TestCase):
    def test_policy_data_is_loaded_and_complete(self):
        policy = load_policy()
        self.assertGreater(len(policy.gazetteer), 900)
        self.assertIn("PDF", {x.upper() for x in policy.gate["ubiquitous"]})
        self.assertIn("AMS", {x.upper() for x in policy.gate["proper_names"]})
        self.assertFalse(policy.b04.get("enabled"))
        self.assertEqual("IO", policy.canonical("I/O"))


class CandidateAndScopeTest(unittest.TestCase):
    def test_plain_english_words_are_not_candidates(self):
        decisions = evaluate_document(doc(
            ("body", "Programming is a skill. CNN model is used.")))
        self.assertEqual(["CNN"], [item.occurrence.token for item in decisions])

    def test_lowercase_words_do_not_match_uppercase_gazetteer(self):
        # 22 篇真实语料实测：In/map/led/go/nb/uri/db 曾被 uppercase 词表回退
        # 误当缩写；F1 是带数字指标，train.py 是文件名，都不应进候选。
        decisions = evaluate_document(doc(
            ("body", "In this paper, F1 and map and led and go and nb and "
                     "uri and db and train.py are ordinary tokens.")))
        self.assertEqual([], decisions)

    def test_gloss_match(self):
        decisions = evaluate_document(doc(
            ("body", "卷积神经网络（CNN）具有很强的拟合能力。")))
        self.assertEqual(1, len(decisions))
        self.assertEqual("match", decisions[0].verdict)

    def test_missing_gloss_is_mismatch_with_evidence(self):
        decisions = evaluate_document(doc(
            ("body", "CNN模型具有很强的拟合能力。")))
        self.assertEqual(1, len(decisions))
        item = decisions[0]
        self.assertEqual("mismatch", item.verdict)
        self.assertEqual(REASON_NO_GLOSS, item.reason_code)
        self.assertIn("gloss_missing", item.rule_hits)
        self.assertEqual("body", item.occurrence.scope)
        self.assertTrue(item.occurrence.first_in_scope)

    def test_scope_is_independent(self):
        decisions = evaluate_document(doc(
            ("en_abstract_body", "Convolutional Neural Network (CNN) is used."),
            ("body", "CNN模型具有很强的拟合能力。"),
        ))
        self.assertEqual(2, len(decisions))
        self.assertEqual(["en_abstract", "body"],
                         [item.occurrence.scope for item in decisions])
        self.assertEqual(["match", "mismatch"],
                         [item.verdict for item in decisions])

    def test_only_first_occurrence_in_scope_is_evaluated(self):
        decisions = evaluate_document(doc(
            ("body", "卷积神经网络（CNN）具有很强的拟合能力。"),
            ("body", "CNN模型仍然存在局限。"),
        ))
        self.assertEqual(1, len(decisions))
        self.assertEqual("match", decisions[0].verdict)

    def test_evaluate_token_can_report_non_first(self):
        document = doc(
            ("body", "卷积神经网络（CNN）具有很强的拟合能力。"),
            ("body", "CNN模型仍然存在局限。"),
        )
        decision = evaluate_token(document, "CNN", block_index=1)
        self.assertEqual("not_applicable", decision.verdict)
        self.assertEqual(REASON_NOT_FIRST, decision.reason_code)

    def test_context_unavailable_when_no_scope(self):
        decisions = evaluate_document(doc(
            (None, "CNN模型具有很强的拟合能力。")))
        self.assertEqual(1, len(decisions))
        self.assertEqual("unknown", decisions[0].verdict)
        self.assertEqual(REASON_CONTEXT, decisions[0].reason_code)
        self.assertIsNone(decisions[0].occurrence.scope)

    def test_no_scope_but_local_gloss_is_match(self):
        decisions = evaluate_document(doc(
            (None, "Convolutional Neural Network (CNN) is used.")))
        self.assertEqual(1, len(decisions))
        self.assertEqual("match", decisions[0].verdict)
        self.assertIsNone(decisions[0].occurrence.first_in_scope)


class GlossAndExemptionTest(unittest.TestCase):
    def test_virtual_dom_is_not_a_real_gloss(self):
        decisions = evaluate_document(doc(
            ("body", "虚拟DOM（Virtual DOM）提升了性能。")))
        self.assertEqual(1, len(decisions))
        self.assertEqual("mismatch", decisions[0].verdict)
        self.assertEqual(REASON_NO_GLOSS, decisions[0].reason_code)

    def test_real_dom_gloss_is_match(self):
        decisions = evaluate_document(doc(
            ("body", "文档对象模型（DOM）是接口。")))
        self.assertEqual(1, len(decisions))
        self.assertEqual("match", decisions[0].verdict)

    def test_proper_names_are_not_applicable(self):
        decisions = evaluate_document(doc(
            ("body", "亚马逊（AMS）与麻省理工学院（MIT）合作。")))
        self.assertEqual({"AMS", "MIT"},
                         {item.occurrence.token for item in decisions})
        self.assertTrue(all(item.verdict == "not_applicable"
                            for item in decisions))
        self.assertTrue(all(item.reason_code == REASON_EXEMPT
                            for item in decisions))

    def test_algorithm_proper_names_are_not_applicable(self):
        # 2026-11-15 人审：K-means / AdaBoost 是算法专名，不是全大写缩略语。
        decisions = evaluate_document(doc(
            ("body", "模型使用K-means算法，并与AdaBoost模型比较。")))
        self.assertEqual({"K-means", "AdaBoost"},
                         {item.occurrence.token for item in decisions})
        self.assertTrue(all(item.verdict == "not_applicable"
                            for item in decisions))
        self.assertTrue(all(item.reason_code == REASON_EXEMPT
                            for item in decisions))

    def test_short_form_and_direct_definition_count_as_gloss(self):
        cases = [
            "采用均方误差(MSE Loss)衡量差异。",
            "随机梯度下降算法（Stochastic Gradient Descent，简称SGD）用于优化。",
            "常见的归一化有批量归一化（Batch Normalization，简称BN）。",
            "层归一化（Layer Normalization，简称LN）用于稳定训练。",
            "FN 是假负例，即预测为负例但实际为正例的数量。",
        ]
        for text in cases:
            with self.subTest(text=text[:12]):
                decisions = evaluate_document(doc(("body", text)))
                self.assertTrue(decisions, text)
                self.assertEqual("match", decisions[0].verdict, text)

    def test_unparenthesized_explanations_are_match(self):
        cases = [
            ("CDN", "为网站添加CDN内容分发网络进行加速。"),
            ("CNN", "GAN网络是继CNN卷积神经网络之后又一大经典神经网络。"),
            ("BP", "其中提出了卷积神经网络、递归神经网络和BP反向传播算法等模型。"),
            ("CNN", "This is another classical neural network after "
                    "CNN convolutional neural network."),
            ("AUC", "AUC值是ROC曲线下面积，表示模型对所有阈值的排序性能。"),
            ("MVC", "MVC设计模式分为数据存储层（Model）、视图层（View）"
                    "和控制层（Controller）。"),
        ]
        for token, text in cases:
            with self.subTest(token=token, text=text[:12]):
                decisions = by_token(evaluate_document(doc(("body", text))),
                                      token)
                self.assertEqual(1, len(decisions), text)
                self.assertEqual("match", decisions[0].verdict, text)

    def test_reference_marker_does_not_break_paren_gloss(self):
        decisions = evaluate_document(doc(
            ("body", "知识图谱（KG[1]）用于组织知识；"
                     "卷积神经网络（CNN[8][9]）用于图像识别。")))
        self.assertEqual({"KG", "CNN"},
                         {item.occurrence.token for item in decisions})
        self.assertTrue(all(item.verdict == "match" for item in decisions))

    def test_pdf_style_gloss_with_token_before_english_name(self):
        """PDF 抽取常写成「（MOOP ，Multi-Objective Optimization Problem）」。"""
        text = "多 目标优化问题（MOOP ，Multi-Objective Optimization Problem） 旨在同时优化。"
        decisions = by_token(evaluate_document(doc(("body", text))), "MOOP")
        self.assertEqual(1, len(decisions))
        self.assertEqual("match", decisions[0].verdict)

    def test_chinese_appositive_gloss_counts_as_explained(self):
        text = "国外流行的一种技术叫PWA，也就是渐进式网络应用，可以安装在手机上。"
        decisions = by_token(evaluate_document(doc(("body", text))), "PWA")
        self.assertEqual(1, len(decisions))
        self.assertEqual("match", decisions[0].verdict)

    def test_appositive_without_explanation_stays_mismatch(self):
        text = "该系统使用PWA技术加载页面，缓存策略由服务工作线程维护。"
        decisions = by_token(evaluate_document(doc(("body", text))), "PWA")
        self.assertEqual(1, len(decisions))
        self.assertEqual("mismatch", decisions[0].verdict)

    def test_model_and_official_format_names_are_na(self):
        decisions = evaluate_document(doc(
            ("body", "模型使用PSPNet，文档解析使用OOXML。")))
        self.assertEqual({"PSPNet", "OOXML"},
                         {item.occurrence.token for item in decisions})
        self.assertTrue(all(item.verdict == "not_applicable"
                            for item in decisions))
        self.assertTrue(all(item.reason_code == REASON_EXEMPT
                            for item in decisions))

    def test_abbreviation_without_expansion_remains_mismatch(self):
        decisions = evaluate_document(doc(
            ("en_abstract_body",
             "This article uses t-SNE clustering algorithm.")))
        self.assertEqual(1, len(decisions))
        self.assertEqual("mismatch", decisions[0].verdict)

    def test_ai_default_is_not_applicable(self):
        decisions = evaluate_document(doc(
            ("body", "AI技术正在快速发展。")))
        self.assertEqual(1, len(decisions))
        self.assertEqual("not_applicable", decisions[0].verdict)
        self.assertEqual(REASON_EXEMPT, decisions[0].reason_code)

    def test_legacy_gate_can_be_disabled_for_parity(self):
        # ICD 只在旧 PROPER 集合里，不在 2027 词表；旧 gate 下 NA，
        # 关掉旧 gate 后应回到 unknown（v5 decide 的口径）。
        with_gate = evaluate_document(doc(("body", "ICD编码用于疾病分类。")))
        without_gate = evaluate_document(
            doc(("body", "ICD编码用于疾病分类。")),
            include_legacy_gate=False)
        self.assertEqual("not_applicable", with_gate[0].verdict)
        self.assertEqual("unknown", without_gate[0].verdict)

    def test_ai_strict_requires_explanation(self):
        decisions = evaluate_document(doc(
            ("body", "AI技术正在快速发展。")), strict=True)
        self.assertEqual(1, len(decisions))
        self.assertEqual("mismatch", decisions[0].verdict)
        self.assertEqual(REASON_NO_GLOSS, decisions[0].reason_code)

    def test_d_level_official_name_is_not_applicable(self):
        decisions = evaluate_document(doc(
            ("body", "Transformer模型改变了序列建模。")))
        self.assertEqual(1, len(decisions))
        self.assertEqual("not_applicable", decisions[0].verdict)
        self.assertEqual(REASON_EXEMPT, decisions[0].reason_code)

    def test_d_level_needing_gloss_is_mismatch(self):
        decisions = evaluate_document(doc(
            ("body", "ViT模型在图像分类上表现良好。")))
        self.assertEqual(1, len(decisions))
        self.assertEqual("mismatch", decisions[0].verdict)
        self.assertEqual(REASON_NO_GLOSS, decisions[0].reason_code)

    def test_b04_is_off_by_default(self):
        decisions = evaluate_document(medical_doc())
        ecg = by_token(decisions, "ECG")
        self.assertEqual(1, len(ecg))
        self.assertEqual("mismatch", ecg[0].verdict)

    def test_b04_can_be_enabled_per_request(self):
        decisions = evaluate_document(medical_doc(), b04_domain_exempt=True)
        ecg = by_token(decisions, "ECG")
        self.assertEqual(1, len(ecg))
        self.assertEqual("not_applicable", ecg[0].verdict)
        self.assertEqual(REASON_EXEMPT, ecg[0].reason_code)

    def test_b04_still_needs_medical_field(self):
        document = doc(("body", "ECG信号用于诊断。"))
        decisions = evaluate_document(document, b04_domain_exempt=True)
        ecg = by_token(decisions, "ECG")
        self.assertEqual(1, len(ecg))
        self.assertEqual("mismatch", ecg[0].verdict)


if __name__ == "__main__":
    unittest.main()
