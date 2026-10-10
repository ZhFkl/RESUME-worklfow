"""Keep a generated resume's style tied to its selected template snapshot."""
import re
from backend.layout_tuning import parameters, LIMITS, uncomment


def _style(source):
    clean = uncomment(source)
    if clean.count(r'\begin{document}') != 1:
        raise ValueError('模板核对失败：需要唯一的 document 起点')
    head, body = clean.split(r'\begin{document}', 1)
    # Only the two documented layout adjustments and photo support may differ.
    head = re.sub(r'\\linespread\s*\{[\d.]+\}', '', head)
    head = re.sub(r'\bbottom\s*=\s*[\d.]+\s*(?:mm|cm|in|pt)\s*,?', '', head)
    head = re.sub(r'\\geometry\s*\{\s*\}', '', head)
    head = re.sub(r'\\usepackage\s*\{graphicx\}', '', head)
    head = re.sub(r',\s*([}\]])', r'\1', head)
    return re.sub(r'\s+', '', head), body


def verify_template(source, template):
    expected, _ = _style(template)
    actual, body = _style(source)
    if actual != expected:
        raise ValueError('模板不一致：文档类型、字体、页边距或样式宏与本次所选模板不同。'
                         '请复制本次 selected_template.source 的完整前导区，仅替换正文；'
                         '排版微调请用 compile_pdf 的 layout_json，不沿用历史简历的样式。')
    old, new = parameters(template), parameters(source)
    for key, value in new.items():
        if value != old[key] and (value is None or not LIMITS[key][0] <= value <= LIMITS[key][1]):
            raise ValueError('模板核对失败：排版调整超出允许范围：' + key)
    names = re.findall(r'\\(?:newcommand|renewcommand|providecommand)\*?\s*\{?\s*(\\[A-Za-z@]+)', uncomment(template).split(r'\begin{document}')[0])
    for name in names:
        if re.search(r'\\(?:newcommand|renewcommand|providecommand|def|let)\*?\s*\{?\s*' + re.escape(name) + r'(?![A-Za-z@])', body):
            raise ValueError('模板核对失败：正文中重新定义了模板样式宏 ' + name)
