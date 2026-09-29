"""pdf_engine —— PDF文字提取引擎（v1.0）

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月9日
"""

import os
from typing import Any






def process(file_path: str, max_pages: int = 10) -> dict[str, Any]:
    """
    提取PDF文件中的文字内容。
    
    Args:
        file_path: PDF文件路径
        max_pages: 最大提取页数（防止超大PDF）
    
    Returns:
        {"text": str, "confidence": float, "source": str, "pages": int, "error": Optional[str]}
    """
    if not os.path.exists(file_path):
        return {
            "text": "",
            "confidence": 0.0,
            "source": "pdf_engine",
            "pages": 0,
            "error": f"文件不存在: {file_path}"
        }
    
    result = {"text": "", "confidence": 0.0, "source": "pdf_engine", "pages": 0, "error": None}
    
    try:
        import fitz  # PyMuPDF
        
        _doc = fitz.open(file_path)
        try:
            _pages = min(len(_doc), max_pages)
            _text_parts = []

            for _page_num in range(_pages):
                _page = _doc[_page_num]
                _page_text = _page.get_text()
                if _page_text and len(_page_text.strip()) > 0:
                    _text_parts.append(f"[第{_page_num + 1}页]\n{_page_text.strip()}")
        finally:
            _doc.close()
        
        if _text_parts:
            result["text"] = "\n\n".join(_text_parts)
            result["confidence"] = 0.95
            result["pages"] = _pages
        else:
            result["error"] = "PDF中未提取到文字内容"
        
    except ImportError:
        result["error"] = "PyMuPDF(fitz)未安装，请运行: pip install PyMuPDF"
    except Exception as e:
        result["error"] = f"PDF提取异常: {str(e)[:80]}"
    
    return result