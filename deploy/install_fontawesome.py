"""Install the small official CTAN Font Awesome 5 package, with a pinned hash.

Avoids texlive-fonts-extra's unrelated fonts. Source/licenses:
https://ctan.org/pkg/fontawesome5 (LPPL 1.3c / SIL OFL 1.1).
"""
import hashlib
import io
from pathlib import Path
import shutil
import zipfile

# Original archive: https://mirrors.ctan.org/fonts/fontawesome5.zip
ARCHIVE = Path('/tmp/fontawesome5.zip')
SHA256 = '83c86c8a92d80e0b2c84af78a055d10798f8294f63b397ba8225351b9eaaa500'
ROOT = Path('/usr/local/share/texmf')
DIRECTORIES = {
    'tex': 'tex/latex/fontawesome5',
    'opentype': 'fonts/opentype/public/fontawesome5',
    'type1': 'fonts/type1/public/fontawesome5',
    'tfm': 'fonts/tfm/public/fontawesome5',
    'enc': 'fonts/enc/dvips/fontawesome5',
    'map': 'fonts/map/dvips/fontawesome5',
    'doc': 'doc/latex/fontawesome5',
}


def main():
    data = ARCHIVE.read_bytes()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise RuntimeError('Font Awesome archive changed; verify the upstream version before updating its hash')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            parts = Path(name).parts
            if name.endswith('/'):
                continue
            if len(parts) == 3 and parts[0] == 'fontawesome5' and parts[1] in DIRECTORIES:
                target = ROOT / DIRECTORIES[parts[1]] / parts[2]
            elif name == 'fontawesome5/README.md':
                target = ROOT / 'doc/latex/fontawesome5/README.md'
            else:
                raise RuntimeError('Unexpected archive member: ' + name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as source, target.open('wb') as destination:
                shutil.copyfileobj(source, destination)


if __name__ == '__main__':
    main()
