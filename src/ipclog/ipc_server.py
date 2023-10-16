"""
Copyright (c) 2011, Vonv
All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.
* Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
import os
import sys
import time
import signal
import logging
import errno
import setproctitle
import multiprocessing as mp
import uuid
from collections import deque
from typing import Callable
from threading import Event
from multiprocessing import Process

# Ref to https://bugs.python.org/issue33725
if sys.platform == "darwin" and sys.version_info.minor >= 8:
    mp.set_start_method(method="fork", force=True)


def pipe_max_size() -> int:
    path = "/proc/sys/fs/pipe-max-size"
    if os.path.exists(path):
        with open(path, 'r') as f:
            return int(f.readline())
    if sys.platform == "darwin":
        return 65536
    return 4096


def ensure_fifo(path):
    if os.path.exists(path):
        os.remove(path)
    os.mkfifo(path)


def register_exit_handler(signal_handler):
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGQUIT, signal_handler)


def dummy_executable(line, context):
    logging.info(f"{line}: {context}")


class IPClient:
    PIPE_MAX = pipe_max_size()
    ATOMIC_MAX = 4096

    def __init__(self, path, nonblock=False, linestep="\r", cache_len=500):
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        self.fifo_path = path
        self.nonblock = nonblock
        self.linestep = linestep
        self._cache = deque(maxlen=cache_len)
        self._no = str(uuid.uuid4())[-4:] + "#"  # done use # in logger
        self._noe = self._no.replace("#", "&")  # done use # in logger
        self._atom_len = self.ATOMIC_MAX - len(self._no) - len(self.linestep)

    def _data_pack(self, d, end=False):
        if end:
            return self._noe + d
        return self._no + d

    def _write(self, line: str) -> None:
        with open(self.fifo_path, 'w', buffering=1024, newline=self.linestep) as pipe:
            pipe.write(line + self.linestep)

    def _write_nonblock(self, line: str) -> None:
        # sample cache 1/2
        if self._cache.__len__() == self._cache.maxlen:
            _d = deque(maxlen=self._cache.maxlen)
            for i in range(0, self._cache.maxlen, 2):
                _d.append(self._cache[i])
            self._cache = _d
        # beyond atom length will use data package
        if len(line) > self._atom_len:
            atm_c = int(len(line) / self._atom_len)
            for i in range(atm_c):
                self._cache.append(self._data_pack(line[i * self._atom_len:(i + 1) * self._atom_len]))
            i += 1
            self._cache.append(self._data_pack(line[i * self._atom_len:(i + 1) * self._atom_len], True))
        else:
            # cache log line to write when ready
            self._cache.append(line)
        # process cache
        fd = None
        while self._cache:
            try:
                fd = os.open(self.fifo_path, os.O_CREAT | os.O_WRONLY | os.O_NONBLOCK)
                data = (self._cache[0] + self.linestep).encode()
                # write bytes
                n = os.write(fd, data)
                # retry if write bytes loss
                if len(data) == n:
                    self._cache.popleft()
                else:
                    self._cache[0] = data[n:].decode()
                os.close(fd)
            except OSError as e:
                if fd:
                    os.close(fd)
                if e.errno in [errno.ENXIO, errno.EPIPE, errno.EWOULDBLOCK]:
                    pass  # print(e)
                else:
                    print(e)
                break
        return

    def write(self, line: str) -> None:
        if self.PIPE_MAX and len(line) > self.PIPE_MAX:
            logging.warning(f"Over {self.PIPE_MAX} bytes FIFO Write is not atomic and may be corrupted")
        if self.nonblock:
            self._write_nonblock(line)
        else:
            self._write(line)

    def flush_cache(self):
        try:
            while self._cache:
                self._write(self._cache.popleft())
        except OSError as e:
            pass


def exit_signal_handler(signum, stack):
    logging.info(f"Process exit with signum: {signum}, stack: {stack}")


class IPCServer:
    """
    This is a very simple IPC server through FIFO
    :param path: FIFO file path
    :param init_logger: func to handle the msg that read from FIFO file
    :param exec_func: func
    """

    def __init__(self, path: str, linestep: str = "\r",
                 inits: Callable = lambda: {}, nonblock=False, cache_len=500,
                 execute: Callable = dummy_executable, final: Callable = None):
        self.fifo_path = path
        self.exit_event = Event()
        self.initer = inits
        self.execute = execute
        self.nonblock = nonblock
        self.cache_len = cache_len
        self.p = None
        self.client = None
        self.final = final
        self.linestep = linestep
        self.saltfish = 0
        self.eof = mp.Value("i", 0)

    def __enter__(self):
        ensure_fifo(self.fifo_path)
        clt = IPClient(self.fifo_path, self.nonblock, self.linestep, self.cache_len)
        self.client = clt
        # try init
        self.initer()
        # start server
        self.p = Process(target=self._run, args=(self.eof,))
        self.p.start()
        logging.info(f"IPC server run in {self.p.pid}")
        # no block read if no log
        clt._write(self.linestep)
        return clt

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.nonblock:
            self.client.flush_cache()
        self.eof.value = 1
        self.p.join(timeout=10)
        os.remove(self.fifo_path)
        self.p = None

    def _run(self, eof) -> None:
        # rename subproc
        setproctitle.setproctitle("ipc_server")
        # intercept the kill signal to guarantee the child process to safely exit
        register_exit_handler(exit_signal_handler)
        context = self.initer()
        buf_mp = {}
        # open would block if write end no data wrote
        with open(self.fifo_path, 'r', buffering=1, newline=self.linestep) as pipe:
            while True:
                try:
                    line = pipe.readline().strip()
                    if line:
                        self.saltfish = 0
                        if line[4] == "#":
                            buf_mp[line[:4]] = buf_mp[line[:4]] + line[5:] if line[:4] in buf_mp else line[5:]
                            continue
                        if line[4] == "&":
                            if line[:4] in buf_mp:
                                k = line[:4]
                                line = buf_mp[k] + line[5:]
                                del buf_mp[k]
                            else:
                                line = line[5:]
                        self.execute(line, context)
                        continue
                except Exception as ex:
                    logging.warning(f"Fail to run ipc call due to {ex}, {line}")

                self.saltfish += 1
                # quit server
                if self.saltfish > 3:
                    if eof.value == 1:
                        break
                    time.sleep(0.1)
        self._quit()

    def _quit(self):
        logging.info(f"IPC Server exits as required")
        if self.final:
            self.final()
