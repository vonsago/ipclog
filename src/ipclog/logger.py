"""
Copyright (c) 2011, Vonv
All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.
* Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
import os
import re
import stat
import pytz
import json
import time
import logging
import datetime
import traceback
import importlib

from datetime import date, datetime
from logging.handlers import RotatingFileHandler
from typing import Callable, ClassVar
from inspect import istraceback
from collections import OrderedDict

TIMEZONE = os.getenv('TIMEZONE', 'Asia/Shanghai')
# skip natural LogRecord attributes
# http://docs.python.org/library/logging.html#logrecord-attributes
RESERVED_ATTRS = (
    'args', 'asctime', 'created', 'exc_info', 'exc_text', 'filename',
    'funcName', 'levelname', 'levelno', 'msecs', 'message', 'msg',
    'name', 'pathname', 'process', 'processName', 'relativeCreated',
    'stack_info', 'thread', 'threadName')
RESERVED_ATTR_HASH = dict(zip(RESERVED_ATTRS, RESERVED_ATTRS))


def is_socket(fpath):
    return os.path.exists(fpath) and stat.S_ISSOCK(os.stat(fpath).st_mode)


class JsonEncoder(json.JSONEncoder):
    """
    A custom encoder extending the default JSONEncoder
    """

    def default(self, obj):
        if isinstance(obj, (date, datetime, time)):
            return self.format_datetime_obj(obj)

        elif istraceback(obj):
            return ''.join(traceback.format_tb(obj)).strip()

        elif type(obj) == Exception \
                or isinstance(obj, Exception) \
                or type(obj) == type:
            return str(obj)

        try:
            return super(JsonEncoder, self).default(obj)

        except TypeError:
            try:
                return str(obj)

            except Exception:
                return None

    def format_datetime_obj(self, obj):
        return obj.isoformat()


class Formatter(logging.Formatter):
    """override logging.Formatter to use an aware datetime object"""

    def converter(self, timestamp):
        tzinfo = pytz.timezone(TIMEZONE)
        return datetime.now(tz=tzinfo)

    def formatTime(self, record, datefmt=None):
        dt = self.converter(record.created)
        if datefmt:
            s = dt.strftime(datefmt)
        else:
            try:
                s = dt.isoformat(timespec='milliseconds')
            except TypeError:
                s = dt.isoformat()
        return s


class JsonFormatter(Formatter):
    """
    A custom formatter to format logging records as json strings.
    extra values will be formatted as str() if nor supported by
    json default encoder
    """

    def __init__(self, *args, **kwargs):
        """
        :param json_default: a function for encoding non-standard objects
            as outlined in http://docs.python.org/2/library/json.html
        :param json_encoder: optional custom encoder
        :param json_serializer: a :meth:`json.dumps`-compatible callable
            that will be used to serialize the log record.
        :param json_indent: an optional :meth:`json.dumps`-compatible numeric value
            that will be used to customize the indent of the output json.
        :param prefix: an optional string prefix added at the beginning of
            the formatted string
        :param rename_fields: an optional dict, used to rename field names in the output.
            Rename message to @message: {'message': '@message'}
        :param static_fields: an optional dict, used to add fields with static values to all logs
        :param static_fields_fresh: extra will update static fields permanent
        :param json_indent: indent parameter for json.dumps
        :param json_ensure_ascii: ensure_ascii parameter for json.dumps
        :param reserved_attrs: an optional list of fields that will be skipped when
            outputting json log record. Defaults to all log record attributes:
            http://docs.python.org/library/logging.html#logrecord-attributes
        :param timestamp: an optional string/boolean field to add a timestamp when
            outputting the json log record. If string is passed, timestamp will be added
            to log record using string as key. If True boolean is passed, timestamp key
            will be "timestamp". Defaults to False/off.
        """
        self.json_default = self._str_to_fn(kwargs.pop("json_default", None))
        self.json_encoder = self._str_to_fn(kwargs.pop("json_encoder", None))
        self.json_serializer = self._str_to_fn(kwargs.pop("json_serializer", json.dumps))
        self.json_indent = kwargs.pop("json_indent", None)
        self.json_ensure_ascii = kwargs.pop("json_ensure_ascii", True)
        self.prefix = kwargs.pop("prefix", "")
        self.rename_fields = kwargs.pop("rename_fields", {
            "message": "msg", "levelname": "level", "asctime": "time"
        })
        self.static_fields = kwargs.pop("static_fields", {})
        self.static_fields_fresh = kwargs.pop("static_fields_fresh", True)
        reserved_attrs = kwargs.pop("reserved_attrs", RESERVED_ATTRS)
        self.reserved_attrs = dict(zip(reserved_attrs, reserved_attrs))
        self.timestamp = kwargs.pop("timestamp", False)
        self.filter = self._str_to_fn(kwargs.pop("filter"))

        super(JsonFormatter, self).__init__(*args, **kwargs)
        if not self.json_encoder and not self.json_default:
            self.json_encoder = JsonEncoder

        self._required_fields = self.parse()
        self._skip_fields = dict(zip(self._required_fields,
                                     self._required_fields))
        self._skip_fields.update(self.reserved_attrs)

    def _str_to_fn(self, fn_as_str):
        """
        If the argument is not a string, return whatever was passed in.
        Parses a string such as package.module.function, imports the module
        and returns the function.
        :param fn_as_str: The string to parse. If not a string, return it.
        """
        if not isinstance(fn_as_str, str):
            return fn_as_str

        path, _, function = fn_as_str.rpartition('.')
        module = importlib.import_module(path)
        return getattr(module, function)

    def merge_record_extra(self, record, target, reserved):
        """
        Merges extra attributes from LogRecord object into target dictionary
        :param record: logging.LogRecord
        :param target: dict to update
        :param reserved: dict or list with reserved keys to skip
        """
        for key, value in record.__dict__.items():
            # this allows to have numeric keys
            if key not in reserved:
                target[key] = value
            if self.static_fields_fresh and key in self.static_fields:
                self.static_fields[key] = value
        return target

    def parse(self):
        """
        Parses format string looking for substitutions
        This method is responsible for returning a list of fields (as strings)
        to include in all log messages.
        """
        standard_formatters = re.compile(r'\((.+?)\)', re.IGNORECASE)
        return standard_formatters.findall(self._fmt)

    def add_fields(self, log_record, record, message_dict):
        """
        Override this method to implement custom logic for adding fields.
        """
        for field in self._required_fields:
            if field in self.rename_fields:
                log_record[self.rename_fields[field]] = record.__dict__.get(field)
            else:
                log_record[field] = record.__dict__.get(field)
        log_record.update(self.static_fields)
        log_record.update(message_dict)
        self.merge_record_extra(record, log_record, self._skip_fields)

        if self.timestamp:
            key = self.timestamp if type(self.timestamp) == str else 'timestamp'
            log_record[key] = datetime.fromtimestamp(record.created, tz=pytz.timezone(TIMEZONE))

    def process_log_record(self, log_record):
        """
        Override this method to implement custom logic
        on the possibly ordered dictionary.
        """
        return log_record

    def jsonify_log_record(self, log_record):
        """Returns a json string of the log record."""
        return self.json_serializer(log_record,
                                    default=self.json_default,
                                    cls=self.json_encoder,
                                    indent=self.json_indent,
                                    ensure_ascii=self.json_ensure_ascii)

    def serialize_log_record(self, log_record):
        """Returns the final representation of the log record."""
        return "%s%s" % (self.prefix, self.jsonify_log_record(log_record))

    def format(self, record):
        """Formats a log record and serializes to json"""
        message_dict = {}
        if isinstance(record.msg, dict):
            message_dict = record.msg
            record.message = None
        else:
            record.message = record.getMessage()
        # only format time if needed
        if "asctime" in self._required_fields:
            record.asctime = self.formatTime(record, self.datefmt)

        # Display formatted exception, but allow overriding it in the
        # user-supplied dict.
        if record.exc_info and not message_dict.get('exc_info'):
            message_dict['exc_info'] = self.formatException(record.exc_info)
        if not message_dict.get('exc_info') and record.exc_text:
            message_dict['exc_info'] = record.exc_text

        try:
            log_record = OrderedDict()
        except NameError:
            log_record = {}

        self.add_fields(log_record, record, message_dict)
        log_record = self.process_log_record(log_record)
        # log_record filter
        if self.filter and not self.filter(log_record):
            return ""
        return self.serialize_log_record(log_record)


class BLogger(logging.Logger):
    def makeRecord(self, name, level, fn, lno, msg, args, exc_info,
                   func=None, extra=None, sinfo=None):
        """
        A factory method which can be overridden in subclasses to create
        specialized LogRecords.
        """
        rv = logging.LogRecord(name, level, fn, lno, msg, args, exc_info, func,
                               sinfo)
        if extra is not None:
            for key in extra:
                # if (key in ["message", "asctime"]) or (key in rv.__dict__):
                #     raise KeyError("Attempt to overwrite %r in LogRecord" % key)
                rv.__dict__[key] = extra[key]
        return rv


def check_logger(ori_func):
    def wrapper_function(*args, **kwargs):
        cnt, logger = ori_func(*args, **kwargs)
        if cnt != len(logger.handlers):
            logging.error(f"init logger error, cnt_handler_num: {cnt}, act_handler_num: {len(logger.handlers)}")
        return logger

    return wrapper_function


@check_logger
def init_logger(
        log_level=logging.INFO,
        add_fields: dict = None,
        forbidden: bool = False,
        files: str | dict = "",
        filters: Callable = None,
        ipc_client: ClassVar = None,
        agent: ClassVar = None,
        fmt="[%(levelname)1.1s %(asctime)s %(module)-16.16s:%(lineno)4d] %(message)s",
        date_fmt="%Y-%m-%d %H:%M:%S"
):
    """
    :param log_level: logging level
    :param add_fields: add init fields
    :param forbidden: forbidden local stdout log
    :param files: [str|**kwargs] 1.FileHandler str 2.RotatingFileHandler **kwargs
    :param filters: lambda record: record.get("msg") will log
    :param ipc_client: ipc client will abort other handler
    :param agent: implement class LogAgentStream: def write(self, msg: str) ...
    :param fmt: log message format
    :param date_fmt: date format
    """
    logger = logging.getLogger()
    logger.__class__ = BLogger
    logger.setLevel(log_level)
    logger.handlers = [logging.NullHandler()]
    handler_num = 1
    formatter = Formatter(fmt, date_fmt)
    # log stdout
    if not forbidden:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        logger.handlers = [handler]
        handler_num = 1
    # files
    if files:
        if type(files) == str:
            file_handler = logging.FileHandler(files)
        if type(files) == dict:
            file_handler = RotatingFileHandler(**files)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        handler_num += 1
    # ipc fifo
    if ipc_client:
        stream = ipc_client
        logging.info(f"Logger detected @ipc_client")
    elif agent:
        stream = agent()
        logging.info(f"Logger detected @{agent}")
    else:
        logging.info("Logger is disabled as no agent detected")
        return handler_num, logger

    formatter = JsonFormatter(fmt=fmt,
                              static_fields=add_fields if add_fields else {},
                              filter=filters if filters else None)
    handler = logging.StreamHandler(stream=stream)
    handler.setFormatter(formatter)
    logger = logging.getLogger()
    logger.setLevel(log_level)
    logger.addHandler(handler)
    handler_num += 1
    return handler_num, logger
