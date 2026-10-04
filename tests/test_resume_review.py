import unittest

from resume_review import layout_metrics, quality_issues


class Page:
    class Box:
        height = 1000
        bottom = 0
    mediabox = Box()

    def __init__(self, bottom=36):
        self.bottom = bottom

    def extract_text(self, visitor_text):
        visitor_text('正文', [1, 0, 0, 1, 0, 0], [1, 0, 0, 1, 0, self.bottom], {}, 10)


class ResumeReviewTests(unittest.TestCase):
    def test_metrics_need_real_emphasis_not_just_label(self):
        source = '单商品处理时间由 5 分钟降至 1 分钟，替代 3 名人工。'
        text = r'\begin{document}\begin{itemize}\item \textbf{上架：}由5分钟降至1分钟，替代3名人工。\end{itemize}\end{document}'
        self.assertEqual(len(quality_issues(text, source, Page())), 3)
        corrected = text.replace('由5分钟降至1分钟', r'\textbf{由5分钟降至1分钟}').replace('替代3名人工', r'\textbf{替代3名人工}')
        self.assertEqual(quality_issues(corrected, source, Page()), [])

    def test_dates_are_not_result_metrics(self):
        text = r'\begin{document}\begin{itemize}\item 项目时间：2026.05—2026.07。\end{itemize}\end{document}'
        self.assertEqual(quality_issues(text, '项目时间：2026.05—2026.07', Page()), [])

    def test_merge_warning_requires_both_source_points_and_room(self):
        source = '## 4. 项目库\n### 分布式系统\n- 项目时间：2026\n- 核心技术：Go\n- Raft 共识实现\n- 容错 KV 实现\n'
        text = r'\begin{document}\resumesection{项目经历}\entry{分布式系统}{2026}\begin{itemize}\item 实现Raft与KV。\end{itemize}\end{document}'
        self.assertTrue(any('素材有至少 2 条' in issue for issue in quality_issues(text, source, Page(190))))
        qualified = text.replace('entry{分布式系统}', 'entry{分布式系统｜个人项目}')
        self.assertTrue(any('素材有至少 2 条' in issue for issue in quality_issues(qualified, source, Page(190))))
        self.assertEqual(quality_issues(text, source, Page(36)), [])
        separated = text.replace(r'\item 实现Raft与KV。', r'\item 实现Raft。\item 实现KV。')
        issues = quality_issues(separated, source, Page(190))
        self.assertEqual(len(issues), 1)
        self.assertIn('超过参考简历', issues[0])

    def test_no_added_items_for_pending_or_sparse_materials(self):
        source = '## 项目经历\n### 个人项目\n- 核心技术：Python\n- 已确认一个事实\n- 【待确认】尚未核实的数字\n'
        text = r'\begin{document}\resumesection{项目经历}\entry{个人项目}{2026}\item 已确认一个事实\end{document}'
        self.assertEqual(quality_issues(text, source, Page()), [])

    def test_whitespace_is_a_gate_even_without_project_headings(self):
        for title in ('Projects', '项目经历', '工作经历'):
            text = r'\begin{document}' + title + ' confirmed fact' + r'\end{document}'
            self.assertEqual(quality_issues(text, '', Page(40)), [])
            issues = quality_issues(text, '', Page(40.1))
            self.assertEqual(len(issues), 1)
            self.assertIn('超过参考简历', issues[0])
            # Covers the old blind spot: 8% was below the previous 14% trigger.
            self.assertTrue(quality_issues(text, '', Page(80)))

    def test_reference_metrics_and_unknown_text(self):
        layout = layout_metrics(Page(36.238701))
        self.assertEqual(layout['bottom_blank_percent'], 3.62)
        self.assertEqual(layout['target_bottom_blank_percent'], 3.62)
        self.assertEqual(layout['max_bottom_blank_percent'], 4.0)
        self.assertTrue(layout['within_whitespace_limit'])

        class EmptyPage(Page):
            def extract_text(self, visitor_text):
                pass
        self.assertFalse(layout_metrics(EmptyPage())['within_whitespace_limit'])
        self.assertTrue(quality_issues(r'\begin{document}\end{document}', '', EmptyPage()))

    def test_commented_bold_cannot_satisfy_check(self):
        text = '\\begin{document}\n\\item 延迟100ms % \\textbf{100ms}\n\\end{document}'
        self.assertEqual(len(quality_issues(text, '延迟100ms', Page())), 1)


if __name__ == '__main__':
    unittest.main()
