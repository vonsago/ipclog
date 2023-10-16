# ipclog
Supports Python multi-process non-blocking log processing to prevent important processes from being blocked due to log processing.

Especially when there are many and important processes, log messages need to be processed at the same time, and these processing processes are time-consuming and may block important processes.
## Must Read Before use
https://man7.org/linux/man-pages/man7/pipe.7.html

Pipe capacity, A pipe has a limited capacity.
    
PIPE_BUF (because of it, Configure the size of `IPClient.ATOMIC_MAX` according to your own server conditions. 
If you really don't know how much to set, you can determine it through experiments which prepared script `tests.test_logger.test_ipc_multi_big_log`)


## How To

```python
from ipclog.logger import init_logger
from ipclog.ipc_server import IPCServer
def init_log():
    il = init_logger(
        filters=lambda record: record.get("msg"), add_fields={"field0": 0}
    )
    return {"ctx": 1, "logger": il}

def exec_log(ll, ctx):
    ctx["logger"].info(ll, extra=json.loads(ll))

with IPCServer("./tmp.fifo", inits=init_log, execute=exec_log, nonblock=True, cache_len=100) as ipc:
    ll = init_logger(ipc_client=ipc)
    for i in range(100):
        ll.info(f"ipc->{i}")
```


## Develop

