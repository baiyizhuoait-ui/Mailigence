"""Tests for the full-body fetch fallback chain (imap_client).

Covers: subject normalisation, Message-ID header matching, and the
From+Subject matching used when a mail has no Message-ID header at all
(synthetic ids) — the strategy that works around servers (QQ) whose
``SEARCH HEADER`` silently matches nothing.
"""

from __future__ import annotations

from app.services.imap_client import ImapClient, _norm_subject


class FakeConn:
    """Minimal imaplib stand-in returning canned UID command responses."""

    def __init__(self, responses: dict[tuple, tuple]):
        self.responses = responses
        self.calls: list[tuple] = []

    def uid(self, *args: str) -> tuple:
        self.calls.append(args)
        return self.responses.get(tuple(args), ("NO", [b""]))


def _make_client(responses: dict[tuple, tuple]) -> ImapClient:
    client = object.__new__(ImapClient)
    client._conn = FakeConn(responses)
    return client


def _header_fetch_response(uid: str, headers: bytes) -> tuple:
    meta = f"{uid} (UID {uid} BODY[HEADER.FIELDS (MESSAGE-ID FROM SUBJECT)] {{{len(headers)}}}".encode()
    return (meta, headers)


def test_norm_subject_strips_reply_prefixes_and_folds_space() -> None:
    assert _norm_subject("Re: Re: FWD:  Hello   World ") == "hello world"
    assert _norm_subject("回复：会议通知") == "会议通知"
    assert _norm_subject("") == ""


def test_norm_subject_decodes_rfc2047() -> None:
    # base64 "5LuK5aSp5oKo5rKh5pyJ" decodes to "今天您没有"
    encoded = "=?UTF-8?B?5LuK5aSp5oKo5rKh5pyJ?="
    assert _norm_subject(encoded) == "今天您没有"


def test_match_uid_by_message_id_header() -> None:
    target = "xnH-i9vHSQqq9ZBwOAg6BA@geopod-ismtpd-132"
    headers = f"Message-ID: <{target}>\r\nFrom: Claude <noreply@claude.ai>\r\n\r\n".encode()
    responses = {
        (
            "FETCH",
            "24,25",
            "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM SUBJECT)])",
        ): (
            "OK",
            [
                _header_fetch_response("24", b"Message-ID: <other@x>\r\n\r\n"),
                _header_fetch_response("25", headers),
                b")",
            ],
        )
    }
    client = _make_client(responses)
    assert (
        client._match_uid_by_header(
            ["24", "25"], target, "noreply@claude.ai", "any subject"
        )
        == "25"
    )


def test_match_uid_by_from_and_subject_for_synthetic_mid() -> None:
    # Mail has NO Message-ID header; DB mid is synthetic. From + RFC2047
    # subject must match.
    headers = (
        b"From: Ads <ads@example.cn>\r\n"
        b"Subject: =?UTF-8?B?6YKu5Lu25Y2g5Y2h55qE5L2/55So?=\r\n\r\n"
    )
    responses = {
        (
            "FETCH",
            "7,8",
            "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM SUBJECT)])",
        ): (
            "OK",
            [
                _header_fetch_response(
                    "7", b"From: other@x.com\r\nSubject: hi\r\n\r\n"
                ),
                _header_fetch_response("8", headers),
            ],
        )
    }
    client = _make_client(responses)
    uid = client._match_uid_by_header(
        ["7", "8"], "synthetic:8", "ads@example.cn", "邮件占卡的使用"
    )
    assert uid == "8"


def test_match_uid_returns_none_when_nothing_matches() -> None:
    headers = b"Message-ID: <different@x>\r\nFrom: a@b.c\r\nSubject: s\r\n\r\n"
    responses = {
        (
            "FETCH",
            "9",
            "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM SUBJECT)])",
        ): ("OK", [_header_fetch_response("9", headers)])
    }
    client = _make_client(responses)
    assert (
        client._match_uid_by_header(["9"], "missing@x", "nomatch@x.com", "other")
        is None
    )
