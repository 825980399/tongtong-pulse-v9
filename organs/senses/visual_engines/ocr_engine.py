"""ocr_engine —— OCR图像文字识别引擎（v1.0）

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月9日
"""

import os
from typing import Any
from config import EXTERNAL_CALL_TIMEOUTS


def engine_name() -> str:
    """返回引擎名称"""
    return "ocr_engine"


def supported_extensions() -> list:
    """返回支持的文件扩展名列表"""
    return ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp', '.tiff']


def process(file_path: str, remote_api_config: dict | None = None) -> dict[str, Any]:
    """
    对图片文件进行OCR文字识别。
    
    Args:
        file_path: 图片文件路径
        remote_api_config: 远程大模型API配置（可选，用于增强识别）
    
    Returns:
        {"text": str, "confidence": float, "source": str, "method": str, "error": Optional[str]}
    """
    if not os.path.exists(file_path):
        return {
            "text": "",
            "confidence": 0.0,
            "source": "ocr_engine",
            "method": "local",
            "error": f"文件不存在: {file_path}"
        }
    
    result = {"text": "", "confidence": 0.0, "source": "ocr_engine", "method": "local", "error": None}
    
    # 第一步：本地Tesseract OCR识别
    try:
        import pytesseract
        from PIL import Image
        
        # 设置Tesseract路径（根据实际安装位置调整）
        _tesseract_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        if os.path.exists(_tesseract_path):
            pytesseract.pytesseract.tesseract_cmd = _tesseract_path
        
        _img = Image.open(file_path)
        
        # ★v23.0优化：图片预处理——灰度化+二值化，提升识别质量
        # 对手机拍照等复杂图片，直接识别效果差
        _img = _img.convert("L")  # 转灰度
        
        # 如果图片太大，缩小到合理尺寸（Tesseract对超大图效果反而差）
        _max_dim = 2000
        if max(_img.size) > _max_dim:
            _ratio = _max_dim / max(_img.size)
            _new_size = (int(_img.size[0] * _ratio), int(_img.size[1] * _ratio))
            _img = _img.resize(_new_size, Image.LANCZOS)
        
        # 检查中文语言包是否可用
        _available_langs = pytesseract.get_languages()
        _has_chinese = 'chi_sim' in _available_langs
        
        if _has_chinese:
            _text = pytesseract.image_to_string(_img, lang='chi_sim+eng')
        else:
            # 中文语言包不可用，仅用英文识别（低精度）
            _text = pytesseract.image_to_string(_img, lang='eng')
        
        # ★v23.0修复：去除中文字符之间的多余空格
        import re as _re_ocr
        if _text:
            # 中文单字之间的空格去掉（如"第 五 部 分" → "第五部分"）
            _text = _re_ocr.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', _text)
            # 中文与标点之间的空格去掉
            _text = _re_ocr.sub(r"""(?<=[\u4e00-\u9fff])\s+(?=[，。、；：！？""''（）])""", '', _text)
            _text = _re_ocr.sub(r"""(?<=[，。、；：！？""''（）])\s+(?=[\u4e00-\u9fff])""", '', _text)
        
        if _text and len(_text.strip()) > 0:
            result["text"] = _text.strip()
            result["confidence"] = 0.75 if _has_chinese else 0.4  # 无中文包时置信度降低
            result["method"] = "tesseract_local"
            if not _has_chinese:
                result["warning"] = "中文语言包(chi_sim)未安装，仅能识别英文。请下载chi_sim.traineddata放入tessdata目录"
            return result
        
    except ImportError:
        result["error"] = "pytesseract未安装"
    except Exception as e:
        result["error"] = f"本地OCR异常: {str(e)[:80]}"
    
    # 第二步：本地OCR失败时，尝试远程大模型增强识别
    if not result["text"] and remote_api_config:
        try:
            _remote_text = _call_remote_ocr(file_path, remote_api_config)
            if _remote_text:
                result["text"] = _remote_text
                result["confidence"] = 0.9
                result["method"] = "remote_api"
                return result
        except Exception as e:
            result["error"] = f"远程OCR异常: {str(e)[:80]}"
    
    return result


def _call_remote_ocr(file_path: str, api_config: dict) -> str:
    """
    调用远程大模型API进行图片文字识别。
    
    将图片转为base64编码，发送给大模型进行识别。
    """
    import base64
    import json as _json
    import urllib.request  # noqa: F401
    
    _api_url = api_config.get("api_url", "")
    _api_key = api_config.get("api_key", "")
    
    if not _api_url or not _api_key:
        return ""
    
    # 读取图片并编码
    with open(file_path, "rb") as f:
        _image_data = base64.b64encode(f.read()).decode("utf-8")
    
    # 构建请求
    _payload = {
        "model": api_config.get("default_model", "deepseek-v4-flash"),
        "messages": [
            {
                "role": "system",
                "content": "你是一个OCR文字识别助手。请识别图片中的所有文字内容，直接输出原文，不要添加任何解释。"
            },
            {
                "role": "user",
                "content": f"请识别以下图片中的文字内容：\n![image](data:image/jpeg;base64,{_image_data})"
            }
        ],
        "temperature": 0.1,
        "max_tokens": 1024,
    }
    
    _payload_bytes = _json.dumps(_payload, ensure_ascii=False).encode('utf-8')
    _headers = {
        'Content-Type': 'application/json; charset=utf-8',
        'Authorization': f'Bearer {_api_key}',
    }
    
    # ★FIX(SSRF): 统一经 ssrf_guard.safe_http_json 出站，禁止绕过防护直连
    from nucleus.ssrf_guard import safe_http_json

    _ok, _data = safe_http_json(
        _api_url, method='POST', data=_payload_bytes, headers=_headers, timeout=EXTERNAL_CALL_TIMEOUTS["http_read"]
    )
    if not _ok:
        return ""
    if isinstance(_data, dict) and _data.get("choices"):
        return _data["choices"][0].get("message", {}).get("content", "").strip()

    return ""