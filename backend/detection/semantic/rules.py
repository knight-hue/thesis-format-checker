# -*- coding: utf-8 -*-
"""语义规则的元数据、分组与 issue 构造。

本模块只放“数据 + 纯构造”，不含任何检查/判定逻辑，
供 checker（编排）与 deterministic（确定性检查）共用。
"""
# 语义类规则清单。requirement/judgement_points 均来自学院规范原文的浓缩与判定要点。
# severity 统一为 warning：RAG 判定置信度低于确定性规则，且多为建议级条款。
SEMANTIC_RULES = [
    {
        "rule_id": "list.numbering",
        "category": "列表格式",
        "severity": "warning",
        "requirement": "列表总项序号：1．、2．；列表分项序号：（1）（2）或数字加括号；列表序号后：不再加其他标点",
        "judgement_points": "检查论文中列举条目是否使用\"1．、2．\"形式的序号；分项是否使用\"（1）（2）\"或数字加括号形式；括号后是否多余地添加了标点符号。",
        "kinds": ["body", "list_item"],
        "message": "列表序号格式不符合要求",
    },
    {
        "rule_id": "terminology",
        "category": "名词术语",
        "severity": "warning",
        "requirement": "名词术语：采用全国自然科学名词审定委员会公布的规范词或国家标准、部标准中规定的名称；外文缩写：首次出现时在括号内注明含义；外国人名：英文原名，名前姓后",
        "judgement_points": "检查外文缩写首次出现时是否在括号内注明了中文含义；外国人名是否按名前姓后书写。",
        "kinds": ["body", "abstract_body", "en_abstract_body",
                  "acknowledgement_body", "list_item", "note"],
        "message": "名词术语或外文缩写使用不符合要求",
    },
    {
        "rule_id": "units",
        "category": "量和单位",
        "severity": "warning",
        "requirement": "量和单位：国家标准GB3100～GB3102-93；非物理量单位：可用汉字与符号构成组合形式的单位",
        "judgement_points": "检查计量单位是否符合 GB3100～GB3102 规定的名称与符号（如 km、kg、m/s）：标准名称被写成俗称（公里、公斤、公分）或单位符号大小写错误时判为不合规；市斤、市尺、市亩等市制单位不属国家标准单位，一律不合规。",
        "kinds": ["body"],
        "message": "量和单位使用不符合国家标准",
    },
    {
        "rule_id": "numerals",
        "category": "数字用法",
        "severity": "warning",
        "requirement": "测量统计数据：阿拉伯数字",
        "judgement_points": "检查测量、统计数据是否使用了阿拉伯数字而非汉字数字（例如应写\"25%\"而非\"百分之二十五\"）。",
        "kinds": ["body", "abstract_body"],
        "message": "测量统计数据未使用阿拉伯数字",
    },
    {
        "rule_id": "formula_axis",
        "category": "图表格式",
        "severity": "warning",
        "requirement": "坐标轴：进行说明；坐标单位：有数字标注的坐标图注明坐标单位",
        "judgement_points": "仅当片段明确涉及坐标图或坐标轴时才需判断：坐标轴是否有说明、有数字标注的坐标图是否注明坐标单位。片段只是转述图中内容（如给出某曲线随某量变化的趋势）而未提及坐标轴或坐标单位时，判为合规。",
        "kinds": ["figure_caption", "body"],
        "message": "坐标轴说明或坐标单位缺失",
    },
    {
        "rule_id": "reference.format",
        "category": "参考文献",
        "severity": "warning",
        "requirement": "文献类型标识：专著[M]、论文集[C]、单篇论文[A]、报纸文章[N]、期刊文章[J]、学位论文[D]、报告[R]、标准[S]、专利[P]、其它[Z]；参考文献著录标准：GB7714—87《文后参考文献著录格式》，详细要求参考GB/T 7714-2005",
        "judgement_points": "检查条目有没有文献类型标识；有条目标识的，按模板给出的该类型样例核对著录项是否齐全、顺序是否正确——样例里排在类型标识之后的部件（句点、出版年份、出版地或保存地、卷期与起止页码）条目里都应当有，出版年份必须排在类型标识之后。",
        "kinds": ["reference_entry"],
        "message": "参考文献著录格式不符合要求",
        # 模板「各类参考文献条目的编排格式及示例如下」逐类给出的样例（原文照录，
        # 前缀保留，便于对照模板）。判据出自这些样例：样例里排在类型标识之后的
        # 部件，条目里就得有；部件在样例中的先后次序就是条目的次序要求。
        "entry_samples": {
            "J": "连续出版物著录格式：［序号］主要责任者．文献题名[J]．刊名，出版年份，卷号(期号)：起止页码．",
            "M": "专著著录格式：［序号］主要责任者．文献题名[M]．出版地：出版者，出版年：起止页码．",
            "A": "会议论文集著录格式：［序号］主要责任者．文献题名[A]∥主编．论文集名[C]．出版地：出版者，出版年：起止页码．",
            "D": "学位论文著录格式：［序号］主要责任．文献题名[D]．保存地：保存单位，年份．",
            "R": "报告著录格式：［序号］主要责任．文献题名[R]．报告地：报告会主办单位，年份．",
            "P": "专利文献著录格式：［序号］专利所有者．专利题名[P]．专利国别：专利号，发布日期．",
            "S": "标准著录格式：［序号］标准代号，标准名称[S]．出版地：出版者，出版年．",
            "N": "报纸文章著录格式：［序号］主要责任者．文献题名[N]．报纸名，出版日期(版次)．",
            "电子文献": "电子文献著录格式：［序号］主要责任者．电子文献题名[文献类型/载体类型]．电子文献的出版或可获得地址，发表或更新的期/引用日期(任选)．",
        },
        # 类型标识 → 该类型样例里必须出现的部件名（逐项取自上面样例）：
        #   mark_period 类型标识后紧跟句点（样例都写作“[J]．刊名”；会议论文集的
        #               分隔符模板写“∥”，GB/T 7714-2005 写“//”，两种都算）
        #   year        出版年份，且必须排在类型标识之后
        #   colon       出版地／保存地／报告地等，与出版者之间用“：”
        #   pages       卷(期)号后的起止页码。只有连续出版物样例把它列为必备部件：
        #               专著整本引用可以不给页码，故 [M] 只要求“：”
        # “其它”：模板没给出该类的编排格式样例（[Z] 是“其它”类），只查 9 条样例
        # 共有的两项，不替它猜结构。
        "entry_features": {
            "J": ("mark_period", "year", "pages"),
            "M": ("mark_period", "year", "colon"),
            "A": ("mark_period", "year", "colon"),
            "D": ("mark_period", "year", "colon"),
            "R": ("mark_period", "year", "colon"),
            "P": ("mark_period", "year", "colon"),
            "S": ("mark_period", "year", "colon"),
            "N": ("mark_period", "year"),
            "电子文献": ("mark_period", "year"),
            "其它": ("mark_period", "year"),
        },
    },
    {
        "rule_id": "acknowledgement.content",
        "category": "致谢",
        "severity": "warning",
        "requirement": "致谢注意事项：不要书写带有时间段的文字",
        "judgement_points": "检查致谢正文中是否出现\"从某年某月到某年某月\"之类的时间段表述。",
        "kinds": ["acknowledgement_body"],
        "message": "致谢中出现带有时间段的文字",
    },
]

# ====================== 规则分组 ======================
# ① 确定性规则：无需 LLM，正则确定性判定，天然覆盖全文、零调用。
#    （致谢/参考文献 + 列表序号/单位/数字三条机械条款，均已确定性化）
#    目录不在此列：config/rules/*.txt 里“目录”整章已撤（模板对目录没有任何说明文字），
#    故目录不做任何检查。
DETERMINISTIC_SEMANTIC_RULES = {
    "acknowledgement.content",
    "reference.format",
    "list.numbering",
    "units",
    "numerals",
}

# ② 定位规则：全文正则扫候选片段，再批量送 LLM 精判（全文覆盖，不受 4000 字符截断限制）。
LOCATED_SEMANTIC_RULES = {
    # 术语已改为「确定性 gate → 残差 LLM」，不再整组走定位规则。
    "formula_axis",
}

# ③ 可文本改写规则：语义 issue 中能靠改写正文解决的（fix 层据此判定 fixable，
#    rag/text_rewrite_service 据此决定是否送 LLM 改写）；结构性规则（图/表位置）
#    不属于文本替换，不在此列。
REWRITABLE_SEMANTIC_RULES = {
    "list.numbering",
    "terminology",
    "units",
    "numerals",
    "formula_axis",
    "acknowledgement.content",
}

# ④ 只能人工处理的语义规则：要补的是实际信息（参考文献缺的卷(期)号、起止页码、
#    出版地），改写无从得知，让模型补写等于编造出处。改写通道整条跳过并给出原因，
#    前端据此显示"为什么不能自动修"。
MANUAL_ONLY_SEMANTIC_RULES = {
    "reference.format": "参考文献的著录项要按原文补全，自动改写会编造出处，请人工补",
}

# 定位规则参数
CANDIDATE_CONTEXT = 80         # 候选点前后各取 80 字符上下文
CANDIDATE_MERGE_GAP = 120      # 相邻候选点间隔小于该值则合并为一个片段
MIXED_BATCH_SIZE = 16          # 合并判定：一次调用包含的片段数（多规则混合）
# 每条定位规则最多送审的候选片段数（按规则差异化：术语需更完整覆盖，
# 坐标轴噪声少、且需结合正文语境，可少送以省调用与耗时）
RULE_MAX_CANDIDATES = {
    "terminology": 20,
    "formula_axis": 12,
}
DEFAULT_MAX_CANDIDATES = 16


def make_issue(rule, blocks, actual, conclusion, basis, suggestion, message=None,
               expected=None, source="rag"):
    """构造与规则引擎一致的 issue 结构。

    source 默认 "rag" 保持既有语义规则行为；术语确定性 gate 传
    "rule_engine"，避免被 _split_advisory 误归到 advisory_issues。

    message 缺省用 rule 里的静态标题；同一条规则因缺失项不同而结论不同时，
    由调用方传入更贴切的标题。expected 缺省用 rule 里的 requirement；同一条规则
    下不同情形该有的样子不同时（如参考文献各类条目的样例），由调用方传入。
    """
    return {
        "rule_id": rule["rule_id"],
        "category": rule["category"],
        "severity": rule["severity"],
        "location": {
            "type": "document",
            "kinds": rule["kinds"],
            "text_preview": (actual or "")[:30],
        },
        "blocks": blocks,
        "actual": actual,
        "expected": expected or rule["requirement"],
        "message": message or rule["message"],
        "suggestion": suggestion,
        "basis": basis,
        "conclusion": conclusion,
        "source": source,
    }
