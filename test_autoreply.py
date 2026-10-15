import base64
import unittest

from autoreply import build_reply, extract_body


def b64(s):
    return base64.urlsafe_b64encode(s.encode()).decode()


class Tests(unittest.TestCase):
    def test_extract_nested_plain_text(self):
        payload = {"mimeType": "multipart/mixed", "parts": [
            {"mimeType": "multipart/alternative", "parts": [
                {"mimeType": "text/html", "body": {"data": b64("<p>hi</p>")}},
                {"mimeType": "text/plain", "body": {"data": b64("hello there")}},
            ]},
        ]}
        self.assertEqual(extract_body(payload), "hello there")
        self.assertEqual(extract_body({"mimeType": "text/html", "body": {"data": b64("x")}}), "")

    def test_reply_threads_and_prefers_reply_to(self):
        msg = build_reply({"from": "a@x.com", "reply-to": "b@x.com", "subject": "Lunch?",
                           "message-id": "<1@x>", "references": "<0@x>"}, "Sure!")
        self.assertEqual(msg["To"], "b@x.com")
        self.assertEqual(msg["Subject"], "Re: Lunch?")
        self.assertEqual(msg["In-Reply-To"], "<1@x>")
        self.assertEqual(msg["References"], "<0@x> <1@x>")
        self.assertEqual(build_reply({"from": "a@x.com", "subject": "RE: hi"}, "ok")["Subject"], "RE: hi")


if __name__ == "__main__":
    unittest.main()
