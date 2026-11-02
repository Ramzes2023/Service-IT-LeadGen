"""Run repository pytest with OS socket operations denied before collection."""
import socket
import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows asyncio needs a loopback socket pair for its internal wakeup pipe.
# Construct only that pair using the saved method; test/application networking
# still hits the deny-all patched connect method below.
_original_connect = socket.socket.connect
if sys.platform == 'win32':
    def local_socketpair(family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0):
        if family != socket.AF_INET or type != socket.SOCK_STREAM or proto != 0:
            raise RuntimeError('OFFLINE_SOCKETPAIR_TYPE_FORBIDDEN')
        with socket.socket(family,type,proto) as listener:
            listener.bind(('127.0.0.1',0))
            listener.listen(1)
            client = socket.socket(family,type,proto)
            try:
                _original_connect(client,listener.getsockname())
                server,_ = listener.accept()
            except BaseException:
                client.close()
                raise
        return server,client
    socket.socketpair = local_socketpair


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
