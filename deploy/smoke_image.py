"""Check a built image with disposable data, real TeX and replacement containers.

Usage: python3 deploy/smoke_image.py --image resume-workflow:2026.10.04-1
No real model API calls, SMS, or existing user data are used.
"""
import argparse
import hashlib
import http.cookiejar
import json
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from uuid import uuid4


def docker(*args, check=True, timeout=60):
    result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError('Docker check failed: ' + ' '.join(args[:3]) + '\n' + result.stdout[-3000:] + result.stderr[-3000:])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    print('Checking image dependencies and both templates...', flush=True)
    result = docker('run', '--rm', '--network', 'none', '--read-only', '--tmpfs', '/tmp:size=1g,mode=1777',
                    '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                    args.image, 'python', '/app/deploy/check_runtime.py', timeout=300)
    print(result.stdout, end='')
    name = 'resume-image-check-' + uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix='resume-image-check-') as directory:
        # Only this disposable directory is writable by the image UID.
        data = Path(directory) / 'data'
        data.mkdir(mode=0o777)
        data.chmod(0o777)
        csrf = ''
        opener = None
        base = ''

        def request(path, body=None, method=None):
            headers = {'X-Resume-Client': 'browser', 'X-CSRF-Token': csrf}
            raw = None
            if body is not None:
                raw = json.dumps(body).encode()
                headers['Content-Type'] = 'application/json'
            req = urllib.request.Request(base+path, data=raw, headers=headers, method=method)
            try:
                with opener.open(req, timeout=10) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as exc:
                return exc.code, json.load(exc)

        try:
            uid = sid = artifact_url = artifact_hash = None
            for iteration in range(2):
                docker('run', '-d', '--name', name, '--read-only', '--tmpfs', '/tmp:size=1g,mode=1777',
                       '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '--memory', '2g', '--cpus', '2',
                       '--mount', f'type=bind,source={data},target=/data', '-p', '127.0.0.1::8000', args.image)
                base = 'http://' + docker('port', name, '8000/tcp').stdout.strip()
                opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
                csrf = ''
                for attempt in range(90):
                    try:
                        status, health = request('/health')
                        if status == 200:
                            break
                    except (OSError, urllib.error.URLError):
                        pass
                    time.sleep(1)
                else:
                    logs = docker('logs', name, check=False)
                    raise RuntimeError('Container did not become healthy: ' + logs.stdout[-2000:] + logs.stderr[-2000:])
                assert not health['restart_required']
                assert request('/materials')[0] == 401
                assert request('/auth/config')[1]['sms_enabled'] is False
                if iteration == 0:
                    # Seed only the disposable test volume; signup requires a real verification provider.
                    docker('exec', name, 'python', '-c', "from backend.session_store import SessionStore; from backend.account_store import AccountStore; AccountStore(SessionStore()).create('deploycheck', 'abc123')")
                credentials = {'identifier': 'deploycheck', 'password': 'abc123'}
                status, auth = request('/auth/login', credentials)
                assert status == 200, auth
                csrf = auth['csrf']
                if iteration == 0:
                    uid = auth['account']['user_id']
                    material = request('/materials')[1]
                    status, saved = request('/materials', {'markdown': '# Deployment persistence test\nConfirmed test facts.', 'revision': material['revision']}, 'PUT')
                    assert status == 200, saved
                    status, session = request('/sessions', {}, 'POST')
                    assert status == 201, session
                    sid = session['session_id']
                    # Seed a completed PDF record using bundled example files. This checks
                    # file/DB persistence without a paid model call or personal material.
                    seed = """import shutil,sys
from pathlib import Path
from backend.session_store import SessionStore
store=SessionStore(); uid,sid=sys.argv[1:]
tid=store.create_task(uid,sid,'Deployment persistence fixture')
output=store.workspace(uid)/'resumes'/tid; output.mkdir(parents=True)
files=[]
for suffix in ('.tex','.pdf'):
    destination=output/('resume'+suffix)
    shutil.copyfile(Path('/app/模板')/('resume-en-template'+suffix),destination)
    files.append(destination)
store.complete(uid,sid,tid,[],'Deployment fixture saved',files)
"""
                    docker('exec', name, 'python', '-c', seed, uid, sid)
                    files = request('/versions')[1]['files']
                    pdf = next(f for f in files if f['name'].endswith('.pdf'))
                    artifact_url = '/sessions/' + sid + '/files/' + pdf['file_id']
                    with opener.open(base+artifact_url, timeout=10) as response:
                        content = response.read()
                        assert content.startswith(b'%PDF')
                        artifact_hash = hashlib.sha256(content).hexdigest()
                else:
                    assert auth['account']['user_id'] == uid
                    assert request('/materials')[1]['markdown'] == '# Deployment persistence test\nConfirmed test facts.'
                    assert any(s['session_id'] == sid for s in request('/sessions')[1]['sessions'])
                    with opener.open(base+artifact_url, timeout=10) as response:
                        assert hashlib.sha256(response.read()).hexdigest() == artifact_hash
                docker('stop', '--time', '15', name)
                docker('rm', name)
            assert (data / 'resume.sqlite3').is_file()
            assert (data / 'auth-secret').is_file()
            print('PASS: seeded-account login, material, session and downloadable PDF persist after container replacement; API authentication enforced.')
        finally:
            docker('rm', '-f', name, check=False)
            # Remove files created by UID 10001 only inside this disposable mount.
            cleanup = 'from pathlib import Path; import shutil; r=Path("/data"); [shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink() for p in r.iterdir()]'
            docker('run', '--rm', '--network', 'none', '--mount', f'type=bind,source={data},target=/data',
                   args.image, 'python', '-c', cleanup)


if __name__ == '__main__':
    main()
