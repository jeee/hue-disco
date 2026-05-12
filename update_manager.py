from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, Optional

APP_FILES = [
    'app_main.py',
    'app_ctl.py',
    'disco_core.py',
    'dtls_hue_stream.py',
    'hue_api.py',
    'config_schema.py',
    'beat_plugin_loader.py',
    'beat_tracker_core.py',
    'bootstrap_hue_credentials.py',
    'patch_diyhue_config.py',
    'apply_diyhue_runtime_patches.py',
    'apply_diyhue_mg21_current.py',
    'start_mg21_daemon.sh',
]
APP_DIRS = [
    'templates',
    'beat_plugins',
]


def _run(cmd, cwd: Path, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def _short(commit: str) -> str:
    return (commit or '')[:10]


class UpdateManager:
    def __init__(self, install_dir: str, source_dir: Optional[str] = None, branch: Optional[str] = None):
        self.install_dir = Path(install_dir).resolve()
        self.source_dir = Path(source_dir or os.environ.get('HUE_DISCO_SOURCE_DIR') or (self.install_dir / 'src' / 'hue-disco')).resolve()
        if not (self.source_dir / '.git').exists() and (self.install_dir / '.git').exists():
            self.source_dir = self.install_dir
        self.branch = branch or os.environ.get('HUE_DISCO_UPDATE_BRANCH') or 'main'
        self._lock = threading.Lock()
        self._cached_status: Dict = {}
        self._last_check = 0.0
        self._updating = False
        self._last_result: Dict = {}

    def _git(self, args, timeout: int = 30) -> subprocess.CompletedProcess:
        return _run(['git', '-c', f'safe.directory={self.source_dir}', *args], self.source_dir, timeout=timeout)

    def _status_base(self) -> Dict:
        return {
            'enabled': (self.source_dir / '.git').exists(),
            'source_dir': str(self.source_dir),
            'install_dir': str(self.install_dir),
            'branch': self.branch,
            'checking': False,
            'updating': self._updating,
            'last_result': self._last_result,
        }

    def check(self, force: bool = False, max_age_s: int = 1800) -> Dict:
        with self._lock:
            now = time.time()
            if not force and self._cached_status and (now - self._last_check) < max_age_s:
                out = dict(self._cached_status)
                out['updating'] = self._updating
                out['last_result'] = self._last_result
                return out

            status = self._status_base()
            if not status['enabled']:
                status.update({'error': 'Update source checkout is not available on this install.', 'available': False})
                self._cached_status = status
                self._last_check = now
                return status

            fetch = self._git(['fetch', '--quiet', 'origin', self.branch], timeout=45)
            if fetch.returncode != 0:
                status.update({'error': (fetch.stderr or fetch.stdout or 'git fetch failed').strip(), 'available': False})
                self._cached_status = status
                self._last_check = now
                return status

            local = self._git(['rev-parse', 'HEAD'])
            remote = self._git(['rev-parse', f'origin/{self.branch}'])
            if local.returncode != 0 or remote.returncode != 0:
                status.update({'error': 'Could not read local or remote git revision.', 'available': False})
                self._cached_status = status
                self._last_check = now
                return status

            local_commit = local.stdout.strip()
            remote_commit = remote.stdout.strip()
            available = bool(local_commit and remote_commit and local_commit != remote_commit)
            status.update({
                'available': available,
                'local_commit': local_commit,
                'remote_commit': remote_commit,
                'local_short': _short(local_commit),
                'remote_short': _short(remote_commit),
                'checked_at': int(now),
                'error': '',
            })
            self._cached_status = status
            self._last_check = now
            return status

    def _copy_tree(self):
        for item in APP_FILES:
            src = self.source_dir / item
            if src.exists():
                dst = self.install_dir / item
                shutil.copy2(src, dst)
                try:
                    st = src.stat()
                    os.chmod(dst, st.st_mode & 0o777)
                except Exception:
                    pass
        for item in APP_DIRS:
            src = self.source_dir / item
            if src.exists():
                dst = self.install_dir / item
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)

    def apply_and_restart_async(self, restart_delay_s: float = 1.0) -> Dict:
        with self._lock:
            if self._updating:
                return {'started': False, 'error': 'Update already running.'}
            self._updating = True

        def worker():
            result = {'ok': False, 'error': ''}
            try:
                status = self.check(force=True, max_age_s=0)
                if status.get('error'):
                    raise RuntimeError(status['error'])
                if not status.get('available'):
                    result = {'ok': True, 'message': 'Already up to date.', 'restarted': False}
                    return
                pull = self._git(['pull', '--ff-only', 'origin', self.branch], timeout=90)
                if pull.returncode != 0:
                    raise RuntimeError((pull.stderr or pull.stdout or 'git pull failed').strip())
                self._copy_tree()
                self._cached_status = {}
                result = {'ok': True, 'message': 'Update installed; restarting Hue Disco.', 'restarted': True}
                time.sleep(max(0.2, restart_delay_s))
                os._exit(0)
            except Exception as exc:
                result = {'ok': False, 'error': str(exc)}
            finally:
                self._last_result = result
                self._updating = False

        threading.Thread(target=worker, daemon=True).start()
        return {'started': True, 'message': 'Update started. Hue Disco will restart if files are updated.'}
