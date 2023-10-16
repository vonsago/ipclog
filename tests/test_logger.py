import multiprocessing
import os
import json
import time
import logging
import setproctitle
from unittest import TestCase

from src.ipclog.logger import init_logger
from src.ipclog.ipc_server import IPCServer, IPClient


def gop_filter(log_record: dict) -> bool:
    if "gop" in log_record:
        return True
    return False


def diag_filter(log_record: dict) -> bool:
    if "edge_node" in log_record:
        return True
    return False


class TestLoggerLog(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        return

    def setUp(self) -> None:
        self.local_file = "./test.log"
        self.ipc_path = "./tmp.fifo"
        self.rotation_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "rotate_dir")

    def tearDown(self) -> None:
        os.system(f"cat {self.local_file}")
        os.system(f"rm {self.local_file}")
        os.system(f"rm -r {self.rotation_path}")

    def test_ipc(self):
        from src.ipclog.ipc_server import IPCServer

        def log():
            init_logger(files=self.local_file)

        with IPCServer("./tmp.fifo", inits=log) as ipc:
            cnt = 0
            while True:
                ipc.write(f"======\n ipc test{cnt}\n =====")
                logging.info(f"{cnt}")
                if cnt > 9:
                    break
                time.sleep(0.1)
                cnt += 1
        time.sleep(0.5)
        print("over")

    def test_ipc_log(self):
        def init_log():
            ipcl = init_logger(filters=lambda record: record.get("msg"),
                               files=self.local_file, add_fields={"field0": 0})
            return {"ctx": 1, "logger": ipcl}

        def exec_log(ll, ctx):
            ctx["logger"].info(ll, extra=json.loads(ll))
            time.sleep(0.02)

        with IPCServer(self.ipc_path, inits=init_log, execute=exec_log, nonblock=True, cache_len=100) as ipc:
            ll = init_logger(ipc_client=ipc)
            for i in range(100):
                ll.info(f"test-->ipc->{i}")
                time.sleep(0.01)
            print("done-mainthread-write---")
        print("done-ipcthread-write---")
        # test raise
        try:
            with IPCServer(self.ipc_path, inits=init_log, execute=exec_log) as ipc:
                init_logger(ipc_client=ipc)
                for i in range(200):
                    logging.info(f"raise-->ipc->{i}")
                    if i == 120:
                        1 / 0
        except Exception as e:
            print("error:", e)

    def test_ipc_multi_big_log(self):
        mock_request = ""
        for i in range(2000):
            mock_request += f"{i}"

        def init_log():
            ipcl = init_logger(filters=lambda record: record.get("msg"),
                               files=self.local_file, add_fields={"field0": 0})
            return {"ctx": 0, "logger": ipcl}

        def exec_log(ll, ctx):
            ctx["logger"].info(ll, extra=json.loads(ll))
            ctx["ctx"] += 1
            print(ctx["ctx"])

        def cli_log(title="none"):
            setproctitle.setproctitle(f"client_log_{title}")
            for i in range(100):
                logging.info(f"{i}-big-->ipc->--{mock_request}")
                time.sleep(0.1)

        with IPCServer(self.ipc_path, inits=init_log, execute=exec_log, nonblock=True, cache_len=10000) as ipc:
            init_logger(ipc_client=ipc, forbidden=True)
            cli_log()
            p1 = multiprocessing.Process(target=cli_log, args=("p1",))
            p2 = multiprocessing.Process(target=cli_log, args=("p2",))
            p1.start()
            p2.start()
            p1.join()
            p2.join()
            print("done")
