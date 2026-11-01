"""Run repository pytest with OS socket operations denied before collection."""
import socket
import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def forbidden(*args, **kwargs):
    raise RuntimeError('OFFLINE_ACCEPTANCE_NETWORK_FORBIDDEN')


socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
socket.create_connection = forbidden
socket.getaddrinfo = forbidden
socket.socket.sendto = forbidden
if hasattr(socket.socket, 'sendmsg'):
    socket.socket.sendmsg = forbidden

# Third-party auto-loaded plugins are outside this repository's offline contract.
os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'

# Required dependencies must already exist; never install or read credentials.
import pytest

with tempfile.TemporaryDirectory(prefix='acceptance-', dir='docs') as runtime:
    sys.exit(pytest.main(['-q', '-p', 'no:cacheprovider',
                         '--basetemp=' + runtime + '/pytest', 'tests']))
