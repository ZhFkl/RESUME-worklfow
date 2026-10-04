"""对模板生成的正文做有限、可解释的检查；不改写事实或模板样式。"""
import re


# 用户指定的 resume-Agent开发.pdf：A4，最后文字基线距纸底 10.7629 mm。
# 所有模板按同一页高比例检查；4% 上限比参考值宽约 1.12 mm（A4）。
REFERENCE_BOTTOM_BLANK_FRACTION = 0.03623870101794773
MAX_BOTTOM_BLANK_FRACTION = 0.04


METRIC = re.compile(
    r"(?:\d+(?:\.\d+)?|数十|数百|数千|数万|[一二两三四五六七八九十]+)\s*"
    r"(?:分钟|毫秒|秒|名人工|名|万条|条|行配置|万\s*QPS|QPS|ms\b|%|percent\b)", re.I)


def unbrace(text, start):
    """Read one balanced LaTeX group, ignoring escaped braces."""
    depth = 1
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if not depth:
                return text[start + 1:index]
        index += 1
    return ""


def plain(text):
    text = re.sub(r"\\([%&_#])", r"\1", text)
    text = re.sub(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?", "", text)
    return re.sub(r"[{}\s]", "", text)


def project_points(markdown):
    """Recognize project headings, excluding metadata and pending facts."""
    projects = {}
    within = False
    title = None
    for line in markdown.splitlines():
        if line.startswith("## "):
            within = bool(re.search(r"项目(?:库|经历)", line))
            title = None
        elif within and line.startswith("### "):
            title = line[4:].strip()
            if "待确认" in title:
                title = None
            else:
                projects[plain(title)] = 0
        elif title and re.match(r"\s*[-*] ", line):
            content = re.sub(r"^\s*[-*]\s+", "", line).replace("**", "")
            if not re.match(r"(?:项目时间|核心技术|技术栈|时间|角色)\s*[：:]", content) and "待确认" not in content:
                projects[plain(title)] += 1
    return projects


def bottom_blank_fraction(page):
    """Estimate blank space from text baselines; not a general visual-layout validator."""
    height = float(page.mediabox.height)
    positions = []

    def visit(text, cm, tm, font, size):
        y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5] - float(page.mediabox.bottom)
        if text.strip() and 0 < y < height:
            positions.append(y)

    page.extract_text(visitor_text=visit)
    return min(positions) / height if positions else 1.0


def layout_metrics(page):
    """统一留白口径：最后文字基线到纸底，含页边距，不是白色像素占比。"""
    fraction = bottom_blank_fraction(page)
    height_mm = float(page.mediabox.height) * 25.4 / 72
    return {
        "bottom_blank_mm": round(fraction * height_mm, 2),
        "bottom_blank_percent": round(fraction * 100, 2),
        "target_bottom_blank_percent": round(REFERENCE_BOTTOM_BLANK_FRACTION * 100, 2),
        "max_bottom_blank_percent": MAX_BOTTOM_BLANK_FRACTION * 100,
        "max_bottom_blank_mm": round(MAX_BOTTOM_BLANK_FRACTION * height_mm, 2),
        "within_whitespace_limit": fraction <= MAX_BOTTOM_BLANK_FRACTION + 1e-9,
    }


def quality_issues(tex, markdown, page):
    """Return actionable issues, not automatic claims about factual completeness."""
    body = tex.partition(r"\begin{document}")[2]
    body = re.sub(r"(?<!\\)%[^\n]*", "", body)
    issues = []
    source = plain(markdown)
    for match in re.finditer(r"\\item(?:\[[^\]]*\])?\s*(.*?)(?=\\item|\\end\{(?:itemize|enumerate|document)\}|\Z)", body, re.S):
        item = match[1]
        bold = [plain(unbrace(item, m.end() - 1)) for m in re.finditer(r"\\textbf\s*\{", item)]
        for metric in METRIC.finditer(plain(item)):
            phrase = metric[0]
            # Only enforce metrics already supported by the material. This is a
            # formatting check, not a semantic fact verifier.
            if phrase in source and not any(phrase in value for value in bold):
                issue = f"成果“{phrase}”缺少正文加粗，请用 \\textbf{{...}} 突出数字及单位。"
                if issue not in issues:
                    issues.append(issue)
    layout = layout_metrics(page)
    if not layout["within_whitespace_limit"]:
        issues.append(
            f"页底留白 {layout['bottom_blank_percent']:.2f}%（{layout['bottom_blank_mm']:.2f} mm），"
            f"超过参考简历的验收上限 {layout['max_bottom_blank_percent']:.2f}%"
            f"（本页 {layout['max_bottom_blank_mm']:.2f} mm；目标 {layout['target_bottom_blank_percent']:.2f}%）。"
            "先对照 JD 与完整素材恢复遗漏事实、项目和实习的独立贡献；"
            "内容充分后微调模板允许的间距，重新编译。不得用无关内容、页脚占位或异常大间距填满；"
            "素材不足时说明需补充的信息，不能把未达标 PDF 当作成品交付。")
        projects = project_points(markdown)
        section = re.search(r"\\resumesection\{项目经历\}(.*?)(?=\\resumesection|\\end\{document\}|\Z)", body, re.S)
        if section:
            entries = list(re.finditer(r"\\entry\{([^{}]+)\}", section[1]))
            for index, entry in enumerate(entries):
                title = plain(entry[1])
                # Templates sometimes append a source or short qualifier to the title.
                candidates = [count for name, count in projects.items()
                              if title == name or any(title.startswith(name + separator)
                                                     for separator in ("｜", "|", "（", "("))]
                required = min(max(candidates, default=0), 3)
                end = entries[index + 1].start() if index + 1 < len(entries) else len(section[1])
                count = len(re.findall(r"\\item\b", section[1][entry.end():end]))
                if required >= 2 and count < required:
                    issues.append(f"项目“{entry[1]}”素材有至少 {required} 条独立要点，正文仅 {count} 条；"
                                  f"页底约 {layout['bottom_blank_percent']:.2f}% 留白，请恢复独立贡献后重新编译。")
    return issues
