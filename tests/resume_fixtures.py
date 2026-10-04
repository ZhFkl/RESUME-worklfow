"""仅用于排版测试的合成文本；不包含用户经历，也不靠页脚或拉大间距填页。"""


def full_page_tex(label='Resume', first_item=None):
    preamble = (r'\documentclass{article}'
                r'\usepackage[a4paper,top=12mm,bottom=10mm,left=13mm,right=13mm]{geometry}'
                r'\usepackage{enumitem}\pagestyle{empty}'
                r'\begin{document}\begin{itemize}[nosep]')
    items = [first_item or label] + [f'Synthetic layout test line {i}.' for i in range(1, 65)]
    return preamble + '\n' + '\n'.join(r'\item ' + item for item in items) + r'\end{itemize}\end{document}'
