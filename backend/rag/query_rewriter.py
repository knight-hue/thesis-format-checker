"""
查询改写模块
1. 反向问题不再补充"应用场景/使用位置"等抽象词，直接保留原问题
2. 关键词补充精简，只补2个最相关的
3. 支持传入学院词汇表（college_vocab），只补知识库中存在的词
4. 口语化转换、句式解析等通用规则保留
"""
import re


class RuleQueryRewriter:
    """规则版查询改写器"""

    # 关键词候选表（精简版：只保留区分度高的候选词）
    keyword_candidates = {
        "标题": ["一级标题", "二级标题", "三级标题", "标题层级"],
        "字体": ["宋体", "黑体", "Times New Roman", "正文字体"],
        "字号": ["三号", "四号", "小四号", "五号", "小三号"],
        "行距": ["1.5倍行距", "行间距"],
        "边距": ["页边距", "左页边距", "右页边距", "页眉边距"],
        "正文": ["正文字体", "正文行距"],
        "摘要": ["摘要标题", "摘要正文", "中文摘要", "英文摘要"],
        "关键词": ["关键词标题", "关键词内容"],
        "图表": ["图题", "表题", "图号", "表号"],
        "图": ["图题", "图号", "图名", "图的位置"],
        "表": ["表题", "表号", "表名", "表的位置"],
        "参考文献": ["期刊", "专著", "学位论文", "连续出版物"],
        "目录": ["目录内容", "目录格式"],
        "公式": ["公式编号", "公式排版"],
        "致谢": ["致谢标题", "致谢正文"],
    }

    # 反向问题模式（匹配后直接保留原问题，不补充抽象词）
    reverse_patterns = [
        (r"(.+?)用在哪些地方", "reverse_attr_to_place"),
        (r"(.+?)是什么标题用的", "reverse_font_to_title"),
        (r"(.+?)是用来干嘛的", "reverse_font_to_usage"),
        (r"(.+?)一般用在哪里", "reverse_attr_to_place"),
        (r"(.+?)是干嘛的", "reverse_font_to_usage"),
        (r"(.+?)有什么用", "reverse_font_to_usage"),
        (r"(.+?)在哪一边", "reverse_attr_to_place"),
        (r"(.+?)是给什么用的", "reverse_font_to_usage"),
        (r"(.+?)是哪种", "reverse_attr_to_type"),
    ]

    def __init__(self):
        pass

    def rewrite(self, question, college_vocab=None):
        """
        改写查询
        Args:
            question: 原始问题
            college_vocab: 学院词汇表（set），用于过滤补充词，只保留知识库中存在的词
        Returns:
            (改写后的查询, 信息字典)
        """
        original = question
        info = {
            "original": original,
            "rewritten": original,
            "changed": False,
            "rule_type": "none"
        }

        # 第一步：应用具体规则
        result, rule_type = self._apply_specific_rules(question, college_vocab)
        if result != question:
            info["rewritten"] = result
            info["changed"] = True
            info["rule_type"] = rule_type
            return result, info

        # 第二步：通用关键词补充
        result = self._supplement_keywords(question, college_vocab)
        if result != question:
            info["rewritten"] = result
            info["changed"] = True
            info["rule_type"] = "keyword_supplement"
            return result, info

        # 都没匹配，返回原问题
        return question, info

    def _apply_specific_rules(self, question, college_vocab=None):
        """应用具体改写规则"""

        # 1. 反向问题：直接保留原问题，不补充抽象词
        for pattern, rule_type in self.reverse_patterns:
            match = re.search(pattern, question)
            if match:
                return question, rule_type

        # 2. 极端简略问题（长度<=4且在关键词表中）
        if len(question) <= 4:
            for key, candidates in self.keyword_candidates.items():
                if key in question:
                    supplemented = self._filter_candidates(candidates, college_vocab, question)
                    if supplemented:
                        return f"{question} {' '.join(supplemented[:2])}", "extreme_brief"

        # 3. 口语化转换
        question = question.replace("啥", "什么")
        question = question.replace("咋", "怎么")
        question = question.replace("嘛", "")
        question = question.replace("呀", "")
        question = question.replace("呢", "")
        question = question.replace("吧", "")
        question = question.replace("啊", "")
        question = question.replace("哦", "")
        question = question.replace("？", "")
        question = question.replace("?", "")
        question = question.replace("！", "")
        question = question.replace("!", "")
        question = question.replace("。", "")
        question = question.replace("，", " ")
        question = question.replace(",", " ")
        question = re.sub(r"\s+", " ", question).strip()

        # 4. "X的Y是什么"句式
        match = re.search(r"(.+?)的(.+?)是什么", question)
        if match:
            x = match.group(1)
            y = match.group(2)
            return f"{x} {y}", "attr_query"

        # 5. "X用什么Y"句式
        match = re.search(r"(.+?)用什么(.+?)", question)
        if match:
            x = match.group(1)
            y = match.group(2)
            supplemented = self._filter_candidates([y], college_vocab, question)
            return f"{x} {y} {' '.join(supplemented)}", "usage_query"

        # 6. "X要Y吗"句式
        match = re.search(r"(.+?)要(.+?)吗", question)
        if match:
            x = match.group(1)
            y = match.group(2)
            return f"{x} {y}", "yes_no_query"

        # 7. "X和Y的区别"对比句式
        match = re.search(r"(.+?)和(.+?)的区别", question)
        if match:
            x = match.group(1)
            y = match.group(2)
            return f"{x} {y}", "comparison_query"

        # 8. 位置问题
        if "在哪" in question or "位置" in question or "上面" in question or "下面" in question:
            supplemented = self._filter_candidates(["上方", "下方"], college_vocab, question)
            return f"{question} {' '.join(supplemented)}", "position_query"

        return question, "none"

    def _supplement_keywords(self, question, college_vocab=None):
        """通用关键词补充（精简版：只补2个最相关的，且只补知识库中存在的词）"""
        supplemented = []
        for key, candidates in self.keyword_candidates.items():
            if key in question:
                filtered = self._filter_candidates(candidates, college_vocab, question)
                for cand in filtered:
                    if cand not in supplemented:
                        supplemented.append(cand)

        if supplemented:
            return f"{question} {' '.join(supplemented[:2])}"
        return question

    def _filter_candidates(self, candidates, college_vocab, question):
        """过滤候选词：只保留知识库中存在、且不在原问题中的词"""
        result = []
        for cand in candidates:
            # 如果传了学院词汇表，只保留在知识库中出现过的词
            if college_vocab is not None and cand not in college_vocab:
                continue
            # 排除已经在问题中的词
            if cand in question:
                continue
            result.append(cand)
        return result