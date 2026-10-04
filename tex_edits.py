"""模板和简历共用的 LaTeX 局部替换校验；不执行文件读写。"""
import hashlib


def source_hash(source):
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def validate_source(source):
    if (not isinstance(source, str) or len(source) > 120000
            or source.count(r"\begin{document}") != 1 or source.count(r"\end{document}") != 1):
        raise ValueError("源码需包含唯一的 document 起止标记，且不超过 12 万字符")


def apply_edits(source, data):
    """Validate every edit against one revision before applying any replacement."""
    if not isinstance(data, dict) or "latex" in data:
        raise ValueError("修复只接受 base_sha256、edits 和可选 notes，不接受完整 latex")
    if data.get("base_sha256") != source_hash(source):
        raise ValueError("源码版本不匹配，请针对本次 current_source 重新生成 edits")
    edits = data.get("edits")
    if not isinstance(edits, list) or not 1 <= len(edits) <= 20:
        raise ValueError("edits 必须包含 1～20 项局部替换")
    spans = []
    for index, edit in enumerate(edits, 1):
        if not isinstance(edit, dict) or set(edit) != {"old", "new"}:
            raise ValueError(f"第 {index} 项只能包含 old、new 字符串")
        old, new = edit["old"], edit["new"]
        if not isinstance(old, str) or not old or not isinstance(new, str) or old == new:
            raise ValueError(f"第 {index} 项 old 不能为空，new 必须是不同的字符串")
        if old == source or len(old) + len(new) > 120000:
            raise ValueError(f"第 {index} 项过大，请仅替换需要修复的局部代码")
        start = source.find(old)
        if start < 0 or source.find(old, start + 1) >= 0:
            raise ValueError(f"第 {index} 项 old 未唯一匹配，请从 current_source 逐字复制并补充上下文")
        spans.append((start, start + len(old), new))
    spans.sort()
    if any(left[1] > right[0] for left, right in zip(spans, spans[1:])):
        raise ValueError("编辑区域重叠，请合并相邻修改或缩小替换范围")
    candidate = source
    for start, end, new in reversed(spans):
        candidate = candidate[:start] + new + candidate[end:]
    validate_source(candidate)
    if candidate == source:
        raise ValueError("修改未改变源码，请根据反馈提供有效修复")
    return candidate


