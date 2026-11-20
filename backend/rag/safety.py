"""输入/输出安全防护：提示词注入检测与输出内容过滤。

所有进入 prompt 的用户文本（问题、论文片段）都必须先过 check_prompt_injection；
模型输出经 check_output_safety 后才返回给调用方。
"""


# ========== 安全防护层 ==========
def check_prompt_injection(text):
    """输入过滤 - 检测提示词注入攻击"""
    dangerous_patterns = [
        "忽略以上指令", "忽略之前的指令", "忽略你之前的", "忽略所有规则",
        "忘记你之前的", "忘记所有规则",
        "你现在是", "现在你的角色是", "从现在开始你是",
        "system prompt", "system instruction",
        "输出你的提示词", "泄露你的指令", "告诉我你的系统提示", "把你的prompt发给我",
        "帮我写病毒", "帮我写木马", "攻击方法", "破解方法", "入侵教程", "怎么盗号",
        "告诉我密码", "泄露隐私", "获取用户信息",
    ]
    text_lower = text.lower()
    for pattern in dangerous_patterns:
        if pattern.lower() in text_lower:
            return True, f"检测到可疑内容：{pattern}"
    return False, "正常"


def check_output_safety(text):
    """输出过滤 - 检测输出内容是否安全"""
    dangerous_patterns = [
        "病毒代码", "木马代码", "攻击代码", "破解教程", "入侵方法", "盗号方法",
        "钓鱼网站", "诈骗方法",
    ]
    for pattern in dangerous_patterns:
        if pattern in text:
            return False, f"输出包含不安全内容：{pattern}"
    return True, "安全"


# ========== RAG 服务 ==========
