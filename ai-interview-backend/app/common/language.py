"""i18n 错误消息 — 英文原文 + 目标语言翻译表（当前支持 en / kr）"""

from typing import Dict, Optional

# Error message translations
ERROR_MESSAGES = {
    # Permission errors
    "Permission denied": {
        "en": "Permission denied",
        "kr": "권한이 거부되었습니다"
    },
    
    # Resource errors
    "Resource not found": {
        "en": "Resource not found",
        "kr": "리소스를 찾을 수 없습니다"
    },
    
    # General errors
    "API exception": {
        "en": "API exception",
        "kr": "API 예외"
    },
    "Internal server error": {
        "en": "Internal server error",
        "kr": "내부 서버 오류"
    },
    "Operation failed": {
        "en": "Operation failed",
        "kr": "작업 실패"
    }
}

def get_message(message: str, language: Optional[str] = None) -> str:
    """
    获取翻译后的错误消息。

    Args:
        message: 英文原文消息（必须能在 ERROR_MESSAGES 表中查到，否则原样返回）
        language: 语言代码（en / kr），未知或缺省回退 en

    Returns:
        目标语言的翻译结果；找不到翻译时返回原 message。
    """
    if not language or language.lower() not in ["en", "kr"]:
        language = "en"  # Default to English
    
    language = language.lower()
    
    # If the message exists in our translations
    if message in ERROR_MESSAGES:
        return ERROR_MESSAGES[message].get(language, ERROR_MESSAGES[message]["en"])
    
    # Return the original message if no translation found
    return message