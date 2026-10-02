"""Unit tests for ImapClient._parse_fetch_response — no network needed.

Covers the QQ (imap.qq.com) FETCH shape where FLAGS/INTERNALDATE arrive as a
bare item *after* the body literal. Dropping that tail left every QQ message
with received_at=NULL, hiding the newest mail from date-sorted lists.
"""
from app.models.email import MailDirection
from app.services.imap_client import ImapClient


def test_standard_shape_parses_internaldate():
    meta = b'1 (UID 101 INTERNALDATE "02-Oct-2026 09:55:12 +0800" FLAGS (\\Seen))'
    raw = b"Message-ID: <a@b>\r\nFrom: x <s@x.com>\r\nSubject: hi\r\n\r\nBody"
    mails = ImapClient._parse_fetch_response([(meta, raw), b")"], MailDirection.INBOX)
    assert len(mails) == 1
    m = mails[0]
    assert m.message_id == "a@b"
    assert m.received_at is not None
    assert m.received_at.year == 2026 and m.received_at.month == 10
    assert m.is_read is True


def test_qq_trailing_bare_items_are_merged():
    # QQ: meta ends at the literal size, FLAGS/INTERNALDATE follow as a bare item.
    meta = b"19 (UID 21 BODY[] {30}"
    raw = b"Message-ID: <qq-1@qq>\r\nFrom: s <s@x.com>\r\nSubject: q\r\n\r\nBody"
    tail = b' FLAGS (\\Seen) INTERNALDATE "02-Oct-2026 09:55:12 +0800")'
    mails = ImapClient._parse_fetch_response(
        [(meta, raw), tail, b")"], MailDirection.INBOX
    )
    assert len(mails) == 1
    m = mails[0]
    assert m.message_id == "qq-1@qq"
    assert m.received_at is not None, "INTERNALDATE from bare tail must be parsed"
    assert m.received_at.day == 2 and m.received_at.hour == 9
    assert m.is_read is True, "FLAGS from bare tail must be honored"


def test_qq_trailing_items_without_flags_are_ignored():
    meta = b"19 (UID 21 BODY[] {30}"
    raw = b"Message-ID: <qq-2@qq>\r\nSubject: q\r\n\r\nBody"
    # A bare item with no FLAGS/INTERNALDATE (unexpected junk) must not be
    # spliced into the previous message's meta.
    mails = ImapClient._parse_fetch_response([(meta, raw), b"junk", b")"], MailDirection.INBOX)
    assert len(mails) == 1
    assert mails[0].message_id == "qq-2@qq"
    assert mails[0].received_at is None


def test_closing_paren_and_empty_items_skipped():
    meta = b"1 (UID 5 BODY[] {10}"
    raw = b"Message-ID: <c@d>\r\n\r\n"
    mails = ImapClient._parse_fetch_response([(meta, raw), b"", b")"], MailDirection.INBOX)
    assert len(mails) == 1


def test_reversed_internaldate_before_flags():
    meta = b'2 (UID 7 INTERNALDATE " 1-Oct-2026 23:01:04 +0800" FLAGS ())'
    raw = b"Message-ID: <r@e>\r\n\r\nBody"
    mails = ImapClient._parse_fetch_response([(meta, raw), b")"], MailDirection.INBOX)
    assert len(mails) == 1
    assert mails[0].received_at is not None
    assert mails[0].received_at.day == 1
    assert mails[0].is_read is False
