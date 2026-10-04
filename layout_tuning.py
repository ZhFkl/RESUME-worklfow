"""模型选择参数，后端只应用这一组参数；不自动搜索，不改正文或样式宏。"""
import json
import math
import re

LIMITS = {"line_spread": [1.0, 1.15], "bottom_mm": [8.0, 14.0]}
BEGIN = "% BEGIN MODEL LAYOUT PARAMETERS"
END = "% END MODEL LAYOUT PARAMETERS"
BLOCK = re.compile(r"(?m)^% BEGIN MODEL LAYOUT PARAMETERS\n.*?^% END MODEL LAYOUT PARAMETERS\n?", re.S)


def uncomment(text):
    return re.sub(r"(?<!\\)%[^\n]*", "", text)


def parameters(tex):
    preamble = uncomment(tex.partition(r"\begin{document}")[0])
    spread = re.findall(r"\\linespread\s*\{([\d.]+)\}", preamble)
    geometry = re.findall(r"\\usepackage\s*\[([^\]]*)\]\s*\{geometry\}|\\geometry\s*\{([^{}]*)\}", preamble)
    bottom = None
    for group in geometry:
        options = group[0] or group[1]
        for key, number, unit in re.findall(r"\b(bottom|margin|vmargin)\s*=\s*([\d.]+)\s*(mm|cm|in|pt)\b", options):
            bottom = round(float(number) * {"mm": 1, "cm": 10, "in": 25.4, "pt": 25.4 / 72.27}[unit], 3)
    return {"line_spread": float(spread[-1]) if spread else None, "bottom_mm": bottom}


def apply_parameters(tex, raw):
    try:
        requested = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError('layout_json 必须是 JSON 对象，例如 {"line_spread":1.08,"bottom_mm":10}') from exc
    if not isinstance(requested, dict) or not requested or set(requested) - LIMITS.keys():
        raise ValueError("仅支持 line_spread、bottom_mm，至少提供一项")
    for key, value in requested.items():
        low, high = LIMITS[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{key} 必须为 {low}～{high} 内的数值")
    if tex.count(r"\begin{document}") != 1:
        raise ValueError("无法定位唯一的 document 起点，未调整参数")
    preamble, marker, body = tex.partition(r"\begin{document}")
    if preamble.count(BEGIN) != preamble.count(END) or preamble.count(BEGIN) > 1:
        raise ValueError("排版参数区块损坏，请先修复源码")
    if "bottom_mm" in requested and not re.search(r"\\usepackage(?:\[[^\]]*\])?\{geometry\}", uncomment(preamble)):
        raise ValueError("模板未加载 geometry，请先用 write_file 加载后再调下边距")
    # Retain the previous override when only one parameter is changed.
    previous = BLOCK.search(preamble)
    values = parameters(previous[0]) if previous else {}
    values = {k: v for k, v in values.items() if v is not None}
    values.update(requested)
    commands = [BEGIN]
    if "line_spread" in values:
        commands.append(r"\linespread{" + str(values["line_spread"]) + "}")
    if "bottom_mm" in values:
        commands.append(r"\geometry{bottom=" + str(values["bottom_mm"]) + "mm}")
    commands.append(END)
    return BLOCK.sub("", preamble).rstrip() + "\n" + "\n".join(commands) + "\n" + marker + body


def guidance(pages, layout, current):
    if pages != 1:
        return ("当前超页。先检查是否仅溢出少量内容；内容完整时可先减小行距或下边距，"
                "不要同时大改正文。使用 compile_pdf 的 layout_json 指定下一组参数后测量。")
    if not layout["within_whitespace_limit"]:
        excess = round(layout["bottom_blank_mm"] - layout["max_bottom_blank_mm"], 2)
        return (f"一页但页底留白超限 {excess} mm。先核对内容完整性；接近上限且行距增加导致超页时，"
                "回到上一组一页行距，尝试将下边距减小 0.5～1 mm。"
                "底部文本基线包含字体高度的影响，不等于 geometry 下边距。每次以实际编译测量为准。")
    return "页数与留白已达标；修复其他 issues（如有），检查本轮截图后再交付。不要继续无目的放大间距。"
