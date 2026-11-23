"""One JSON object per log line, so logs can be filtered and counted for the evaluation."""
import json
import logging
import sys


def setup(name: str, instance: str) -> logging.Logger:
    class JsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            entry = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname,
                     "service": name, "instance": instance, "msg": record.getMessage()}
            entry.update(getattr(record, "fields", {}))
            return json.dumps(entry)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return logging.getLogger(name)
