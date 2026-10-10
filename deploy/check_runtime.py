"""Image-local dependency and template check; no model API or personal data."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

from pypdf import PdfReader


def main():
    root = Path('/app')
    assert os.getuid() != 0, 'Image must run without root'
    assert not (root / '.env').exists(), 'Secret configuration must not be baked in'
    assert not (root / 'data').exists(), 'User data must not be baked in'
    for executable in ('xelatex', 'pdftoppm', 'kpsewhich'):
        assert shutil.which(executable), f'Missing {executable}'
    for name in ('fontawesome5.sty', 'FandolSong-Regular.otf', 'texgyretermes-regular.otf'):
        subprocess.run(['kpsewhich', name], check=True, capture_output=True)
    for template in ('resume-中文模板.tex', 'resume-en-template.tex'):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'resume.tex'
            shutil.copyfile(root / '模板' / template, source)
            for _ in range(2):
                result = subprocess.run(['xelatex', '-no-shell-escape', '-halt-on-error', '-interaction=nonstopmode', source.name],
                                        cwd=directory, capture_output=True, timeout=120,
                                        env={**os.environ, 'openin_any': 'p', 'openout_any': 'p'})
                if result.returncode:
                    raise RuntimeError(template + ': ' + result.stdout.decode(errors='replace')[-2000:])
            pdf = source.with_suffix('.pdf')
            assert len(PdfReader(pdf).pages) == 1, template
            subprocess.run(['pdftoppm', '-f', '1', '-singlefile', '-scale-to', '600', '-png', str(pdf), str(Path(directory)/'preview')],
                           check=True, capture_output=True, timeout=45)
            assert (Path(directory)/'preview.png').stat().st_size > 0
        print('Template compilation and screenshot passed:', template)


if __name__ == '__main__':
    main()
