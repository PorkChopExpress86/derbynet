"""The verify skill's cleanup returns only once the django server's port is
free, so a launch straight after it finds the port free.

#537's implementer twice saw launch refuse with "port 8137 is taken" right
after a cleanup, which killed runserver and returned at once (#537 retro).
These runs give cleanup a stand-in server that holds its port for a second
after its SIGTERM, as a server on a loaded machine may.

Run: python3 -I -m unittest discover -s plan/tests
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import k_patterns  # noqa: F401  a -k that matches no test is refused

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / '.claude' / 'skills' / 'verify' / 'bin'
# Listens on a free port, prints it, and on SIGTERM holds it 1 s more.
SERVER = '''
import signal, socket, sys, time
s = socket.socket()
s.bind(("127.0.0.1", 0))
s.listen()
print(s.getsockname()[1], flush=True)
signal.signal(signal.SIGTERM, lambda *_: (time.sleep(1), sys.exit(0)))
time.sleep(60)
'''


class CleanupWaitsForThePort(unittest.TestCase):
    def test_the_port_is_free_when_cleanup_returns(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            shutil.copytree(BIN, tmp / '.claude' / 'skills' / 'verify' / 'bin')
            state = tmp / 'server' / 'var' / 'verify-t'
            state.mkdir(parents=True)
            server = subprocess.Popen([sys.executable, '-c', SERVER],
                                      stdout=subprocess.PIPE, text=True)
            self.addCleanup(server.stdout.close)
            self.addCleanup(server.wait)
            self.addCleanup(server.kill)
            port = server.stdout.readline().strip()
            (state / 'runserver.pid').write_text(f'{server.pid}\n')
            env = {**os.environ, 'VERIFY_SERVER': 'django', 'VERIFY_RUN': 't',
                   'VERIFY_PORT': port}
            started = time.monotonic()
            run = subprocess.run([str(tmp / '.claude' / 'skills' / 'verify' / 'bin' / 'cleanup')],
                                 capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIsNotNone(server.poll(), 'the server still runs after cleanup')
            listening = subprocess.run(['ss', '-ltn'], capture_output=True, text=True).stdout
            self.assertNotIn(f'127.0.0.1:{port} ', listening)
            self.assertGreaterEqual(time.monotonic() - started, 1)
            self.assertFalse(state.exists())

