"""Conservative text editing: map visible characters back to original TeX ranges.

This is not a TeX interpreter. Unknown macros stay untouched and are reported;
the preamble and formatting commands are never regenerated from plain text.
"""
import re
from difflib import SequenceMatcher


ESCAPES = {'%': r'\%', '&': r'\&', '#': r'\#', '_': r'\_', '$': r'\$',
           '{': r'\{', '}': r'\}', '\\': r'\textbackslash{}',
           '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}
INLINE = {'textbf', 'textmd', 'textit', 'emph', 'underline', 'textrm', 'textsf', 'texttt',
          'textnormal', 'textsc', 'mbox', 'dsl'}
DECLARATIONS = {'noindent', 'indent', 'small', 'large', 'Large', 'LARGE', 'huge', 'Huge',
                'tiny', 'scriptsize', 'footnotesize', 'normalsize', 'bfseries', 'mdseries',
                'itshape', 'upshape', 'scshape', 'rmfamily', 'sffamily', 'ttfamily',
                'raggedleft', 'raggedright', 'centering', 'normalfont', 'selectfont', 'ignorespaces'}
SKIP = {'vspace': 1, 'hspace': 1, 'color': 1, 'CJKfamily': 1, 'fontsize': 2,
        'includegraphics': 1, 'rule': 2, 'setlength': 2, 'addtolength': 2,
        'linespread': 1, 'geometry': 1, 'pagestyle': 1, 'thispagestyle': 1,
        'IfFileExists': 3, 'newcommand': 2, 'renewcommand': 2, 'providecommand': 2}
SYMBOLS = {'textbackslash': '\\', 'textasciitilde': '~', 'textasciicircum': '^',
           'LaTeX': 'LaTeX', 'TeX': 'TeX'}


def escape_text(value):
    return ''.join(ESCAPES.get(c, ' ' if c in '\r\n\t' else c) for c in value)


class TextDocument:
    def __init__(self, source):
        self.source = source
        self.fields = []
        self.pending = []
        self.section = '基本信息'
        self.label = '文字'
        self.partial = False
        # Retain offsets while excluding comments when finding the document body.
        masked = re.sub(r'(?<!\\)%[^\n]*', lambda m: ' ' * len(m[0]), source)
        body = re.search(r'\\begin\s*\{document\}', masked)
        end = re.search(r'\\end\s*\{document\}', masked)
        if body and end and end.start() > body.end():
            self.walk(body.end(), end.start())
            self.flush()
        else:
            self.partial = True

    def group(self, pos, limit, opening='{', closing='}'):
        s = self.source
        while pos < limit and s[pos].isspace():
            pos += 1
        if pos >= limit or s[pos] != opening:
            return None
        start, depth = pos + 1, 1
        pos += 1
        while pos < limit:
            if s[pos] == '\\':
                pos += 2
                continue
            if s[pos] == '%':
                nl = s.find('\n', pos, limit)
                pos = limit if nl < 0 else nl + 1
                continue
            if s[pos] == opening:
                depth += 1
            elif s[pos] == closing:
                depth -= 1
                if depth == 0:
                    return start, pos, pos + 1
            pos += 1
        self.partial = True
        return None

    def options(self, pos, limit):
        while True:
            part = self.group(pos, limit, '[', ']')
            if not part:
                return pos
            pos = part[2]

    def flush(self):
        while self.pending and self.pending[0]['text'].isspace():
            self.pending.pop(0)
        while self.pending and self.pending[-1]['text'].isspace():
            self.pending.pop()
        if self.pending:
            self.fields.append({'id': str(len(self.fields)), 'section': self.section,
                                'label': self.label, 'text': ''.join(c['text'] for c in self.pending),
                                'chars': self.pending})
        self.pending = []

    def add(self, start, end, text, bold=False):
        self.pending.append({'start': start, 'end': end, 'text': text, 'bold': bold})

    def walk(self, start, end, bold=False, depth=0):
        if depth > 80:
            self.partial = True
            return
        s, i = self.source, start
        while i < end:
            c = s[i]
            if c == '%':
                nl = s.find('\n', i, end)
                i = end if nl < 0 else nl + 1
                continue
            if c == '{':
                part = self.group(i, end)
                if not part:
                    break
                self.walk(part[0], part[1], bold, depth + 1)
                i = part[2]
                continue
            if c in '&~':
                self.flush(); i += 1
                continue
            if c == '$':
                marker = '$$' if s[i:i+2] == '$$' else '$'
                finish = s.find(marker, i + len(marker), end)
                if finish < 0:
                    self.partial = True
                    break
                if s[i+len(marker):finish].strip() != '|':
                    self.partial = True
                self.flush(); i = finish + len(marker)
                continue
            if c != '\\':
                if c == '}':
                    self.partial = True; i += 1
                    continue
                if c.isspace():
                    j = i + 1
                    while j < end and s[j].isspace():
                        j += 1
                    if s[i:j].count('\n') >= 2:
                        self.flush()
                    else:
                        self.add(i, j, ' ', bold)
                    i = j
                else:
                    self.add(i, i+1, c, bold); i += 1
                continue
            begin = i
            match = re.match(r'\\([A-Za-z@]+|.)', s[i:end], re.DOTALL)
            if not match:
                break
            command = match[1]; i += len(match[0])
            if command in '%&#_${}':
                self.add(begin, i, command, bold)
                continue
            if command in SYMBOLS:
                empty = self.group(i, end)
                if empty and empty[0] == empty[1]:
                    i = empty[2]
                # Multi-character logos are kept as atomic runs.
                self.add(begin, i, SYMBOLS[command], bold)
                continue
            if command in {'\\', 'par', 'newline', 'hfill', 'quad', 'qquad', 'textbar', 'textbullet'}:
                self.flush(); i = self.options(i, end)
                continue
            if command.isspace() or command in {',', ';', '!', ':'}:
                self.flush()
                continue
            if command == 'bfseries':
                bold = True
            if command in {'mdseries', 'normalfont'}:
                bold = False
            if command in DECLARATIONS or command.startswith('fa'):
                while i < end and s[i].isspace():
                    i += 1
                continue
            if i < end and s[i] == '*':
                i += 1
            i = self.options(i, end)
            if command in SKIP:
                for _ in range(SKIP[command]):
                    part = self.group(i, end)
                    if part:
                        i = self.options(part[2], end)
                continue
            if command in {'begin', 'end'}:
                self.flush()
                part = self.group(i, end)
                if not part:
                    self.partial = True
                    break
                env = s[part[0]:part[1]]; i = self.options(part[2], end)
                extra = {'minipage': 1, 'tabular': 1, 'tabular*': 2, 'tabularx': 2}.get(env, 0)
                if command == 'begin':
                    for _ in range(extra):
                        part = self.group(i, end)
                        if part:
                            i = part[2]
                    if env not in {'minipage', 'tabular', 'tabular*', 'tabularx', 'center', 'flushleft', 'flushright',
                                   'itemize', 'enumerate', 'description', 'entrybullets'}:
                        closing = s.find('\\end{' + env + '}', i, end)
                        i = end if closing < 0 else closing + len(env) + 6
                        self.partial = True
                continue
            if command == 'item':
                self.flush(); self.label = '要点'
                continue
            if command in INLINE or command == 'textcolor':
                if command == 'textcolor':
                    color = self.group(i, end)
                    if color:
                        i = color[2]
                part = self.group(i, end)
                if part:
                    self.walk(part[0], part[1], False if command in {'textmd', 'textnormal'} else bold or command in {'textbf', 'dsl'}, depth + 1)
                    i = part[2]
                continue
            if command == 'href':
                self.flush()
                target = self.group(i, end)
                title = self.group(target[2], end) if target else None
                if target and title:
                    count = len(self.fields)
                    self.label = '联系方式 / 链接'
                    self.walk(title[0], title[1], bold, depth + 1)
                    self.flush()
                    if len(self.fields) == count + 1:
                        self.fields[-1]['link'] = {'start': target[0], 'end': target[1], 'raw': s[target[0]:target[1]]}
                    i = title[2]
                continue
            if command in {'resumesection', 'section', 'subsection', 'entry', 'entryheading', 'techline'}:
                self.flush()
                labels = {'entry': ['名称', '时间'], 'entryheading': ['名称', '地点 / 时间', '专业 / 职位', '时间'],
                          'techline': ['核心技术']}.get(command, ['章节标题'])
                for label in labels:
                    part = self.group(i, end)
                    if not part:
                        break
                    self.label = label
                    before = len(self.fields)
                    self.walk(part[0], part[1], bold, depth + 1); self.flush()
                    if label == '章节标题' and len(self.fields) > before:
                        self.section = self.fields[-1]['text']; self.fields[-1]['section'] = self.section
                    i = part[2]
                self.label = '文字'
                continue
            # Preserve unsupported commands and their arguments without showing them as prose.
            self.flush(); self.partial = True
            if command in {'def', 'edef', 'gdef', 'xdef', 'catcode', 'let'}:
                nl = s.find('\n', i, end); i = end if nl < 0 else nl + 1
            else:
                while True:
                    part = self.group(i, end)
                    if not part:
                        break
                    i = part[2]

    def public(self):
        fields = []
        for field in self.fields:
            spans, offset = [], 0
            for char in field['chars']:
                if char['bold']:
                    if spans and spans[-1][1] == offset:
                        spans[-1][1] += len(char['text'])
                    else:
                        spans.append([offset, offset + len(char['text'])])
                offset += len(char['text'])
            fields.append({key: field[key] for key in ('id', 'section', 'label', 'text')} | {'bold': spans})
        return {'fields': fields, 'partial': self.partial}

    def format_selection(self, field_id, start, end, bold):
        field = next((f for f in self.fields if f['id'] == field_id), None)
        if (field is None or type(start) is not int or type(end) is not int or type(bold) is not bool
                or not 0 <= start < end <= len(field['text'])):
            raise ValueError('请在同一段正文内选中文字后设置加粗')
        chars = [char for char in field['chars'] for _ in char['text']]
        if ((start and chars[start] is chars[start-1]) or
                (end < len(chars) and chars[end] is chars[end-1])):
            raise ValueError('特殊文字命令需要完整选中后设置加粗')
        spans, seen = [], set()
        for char in chars[start:end]:
            if char['bold'] == bold:
                continue
            span = (char['start'], char['end'])
            if span in seen:
                continue
            seen.add(span)
            if spans and char['start'] == spans[-1][1]:
                spans[-1][1] = char['end']
            else:
                spans.append([char['start'], char['end']])
        content = self.source
        command = r'\textbf{' if bold else r'\textmd{'
        for a, b in reversed(spans):
            content = content[:a] + command + content[a:b] + '}' + content[b:]
        if len(content) > 500000:
            raise ValueError('修改后的源文件不能超过 50 万字符')
        return content

    def edit(self, edits):
        if not isinstance(edits, dict) or set(edits) - {field['id'] for field in self.fields}:
            raise ValueError('正文段落已变化，请重新打开编辑器')
        replacements = []
        for field in self.fields:
            new = edits.get(field['id'], field['text'])
            if not isinstance(new, str) or len(new) > 10000:
                raise ValueError('每段正文不得超过 10000 字符')
            old = field['text']
            if new == old:
                continue
            # Every visible character knows its original source span (including escaped symbols).
            chars = [char for char in field['chars'] for _ in char['text']]
            changes = list(SequenceMatcher(None, old, new).get_opcodes())
            for tag, a, b, x, y in changes:
                if tag == 'equal':
                    continue
                # Refuse to split an atomic macro such as \LaTeX; simple text can use advanced mode.
                if (a > 0 and a < len(chars) and chars[a] is chars[a-1]) or (b > 0 and b < len(chars) and chars[b] is chars[b-1]):
                    raise ValueError('这段包含特殊文字命令，请使用高级源码修改')
                position = chars[a]['start'] if a < len(chars) else chars[-1]['end']
                seen = set()
                for char in chars[a:b]:
                    span = (char['start'], char['end'])
                    if span not in seen:
                        replacements.append((*span, '')); seen.add(span)
                replacements.append((position, position, escape_text(new[x:y])))
            link = field.get('link')
            if link:
                target = link['raw'].replace(r'\_', '_').replace(r'\&', '&').replace(r'\%', '%')
                prefix = next((p for p in ('mailto:', 'https://', 'http://', '') if target == p + old), None)
                if prefix is not None:
                    replacements.append((link['start'], link['end'], escape_text(prefix + new)))
        pieces, cursor = [], 0
        # Insertions precede deletions at the same boundary; untouched commands are copied verbatim.
        for start, end, value in sorted(replacements, key=lambda r: (r[0], r[1])):
            if start < cursor:
                raise ValueError('特殊排版的文字范围重叠，请使用高级源码修改')
            pieces.extend((self.source[cursor:start], value))
            cursor = end
        pieces.append(self.source[cursor:])
        revised = ''.join(pieces)
        if len(revised) > 500000:
            raise ValueError('修改后的源文件不能超过 50 万字符')
        return revised


def text_document(content, edits=None, formatting=None):
    if not isinstance(content, str) or not content.strip() or len(content) > 500000:
        raise ValueError('正文源文件需为 1～50 万个字符')
    document = TextDocument(content)
    old_fields = document.fields
    if edits is not None:
        content = document.edit(edits)
        document = TextDocument(content)
    selection = None
    if formatting is not None:
        if not isinstance(formatting, dict):
            raise ValueError('加粗设置格式无效')
        field_id = formatting.get('field_id')
        original = next((f for f in old_fields if f['id'] == field_id), None)
        if original is None:
            raise ValueError('正文段落已变化，请重新选中文字')
        # Text edits retain field boundaries; account for paragraphs cleared in the same draft.
        expected = [(f['id'], re.sub(r'\s+', ' ', (edits or {}).get(f['id'], f['text'])).strip()) for f in old_fields]
        expected = [(ident, value) for ident, value in expected if value]
        if [value for _, value in expected] != [f['text'] for f in document.fields]:
            raise ValueError('文字排版已变化，请先同步到高级源码，再返回正文重新选择')
        index = next((i for i, (ident, _) in enumerate(expected) if ident == field_id), None)
        raw = (edits or {}).get(field_id, original['text'])
        if index is None or raw != document.fields[index]['text']:
            raise ValueError('选区包含多余换行或空格，请先同步到高级源码，再返回正文重新选择')
        selection = {**formatting, 'field_id': document.fields[index]['id']}
        content = document.format_selection(selection['field_id'], selection.get('start'), selection.get('end'), selection.get('bold'))
        document = TextDocument(content)
    return {'content': content, **document.public(), 'selection': selection}
