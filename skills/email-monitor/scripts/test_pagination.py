#!/usr/bin/env python3
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("email_daemon_pagination", Path(__file__).with_name("daemon.py"))
daemon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(daemon)


class Request:
    def __init__(self, payload): self.payload = payload
    def execute(self): return self.payload


class Messages:
    def __init__(self): self.calls = []
    def list(self, **kwargs):
        self.calls.append(kwargs)
        if "pageToken" not in kwargs:
            return Request({"messages": [{"id": "1"}], "nextPageToken": "next"})
        return Request({"messages": [{"id": "2"}]})


class Service:
    def __init__(self): self.messages_api = Messages()
    def users(self): return self
    def messages(self): return self.messages_api


class Pagination(unittest.TestCase):
    def test_all_pages_are_returned(self):
        svc = Service()
        result = daemon._gmail_list_all(svc, "query", page_size=20)
        self.assertEqual([x["id"] for x in result], ["1", "2"])
        self.assertEqual(svc.messages_api.calls[1]["pageToken"], "next")


if __name__ == "__main__":
    unittest.main()
