from __future__ import annotations

import logging

import keyring
from imapclient import IMAPClient

from postowl.email.parser import parse_email, parse_email_headers
from postowl.models import Email, EmailAccount

logger = logging.getLogger(__name__)

KEYRING_SERVICE = "postowl"


def store_password(email_addr: str, password: str) -> None:
    keyring.set_password(KEYRING_SERVICE, email_addr, password)


def get_password(email_addr: str) -> str | None:
    return keyring.get_password(KEYRING_SERVICE, email_addr)


def delete_password(email_addr: str) -> None:
    try:
        keyring.delete_password(KEYRING_SERVICE, email_addr)
    except keyring.errors.PasswordDeleteError:
        pass


class EmailClient:
    def __init__(self, account: EmailAccount):
        self.account = account
        self._client: IMAPClient | None = None

    def connect(self) -> None:
        password = get_password(self.account.email)
        if not password:
            raise ValueError(
                f"No password found for {self.account.email}. "
                f"Use 'postowl accounts add' to configure."
            )
        self._client = IMAPClient(
            self.account.imap_server,
            port=self.account.imap_port,
            ssl=self.account.use_ssl,
        )
        self._client.login(self.account.username, password)
        logger.info("Connected to %s as %s", self.account.imap_server, self.account.username)

    def disconnect(self) -> None:
        if self._client:
            try:
                self._client.logout()
            except Exception:
                pass
            self._client = None

    def fetch_new_emails(self, since_uid: int = 0, folder: str = "INBOX",
                         limit: int = 200, headers_only: bool = False) -> list[Email]:
        if not self._client:
            raise RuntimeError("Not connected. Call connect() first.")

        self._client.select_folder(folder, readonly=True)

        if since_uid > 0:
            criteria = [u"UID", f"{since_uid + 1}:*"]
        else:
            criteria = ["ALL"]

        uids = self._client.search(criteria)
        if since_uid > 0:
            uids = [u for u in uids if u > since_uid]

        if not uids:
            logger.info("No new emails for %s", self.account.email)
            return []

        uids = sorted(uids)[-limit:]
        logger.info("Fetching %d new emails for %s", len(uids), self.account.email)

        if headers_only:
            data_items = [b"BODY[HEADER]"]
        else:
            data_items = ["RFC822"]

        messages = self._client.fetch(uids, data_items)
        emails: list[Email] = []
        for uid, data in messages.items():
            if headers_only:
                raw = data.get(b"BODY[HEADER]")
                if not raw:
                    continue
                parsed = parse_email_headers(raw, self.account.id or 0, uid)
            else:
                raw = data.get(b"RFC822")
                if not raw:
                    continue
                parsed = parse_email(raw, self.account.id or 0, uid)
            if parsed:
                emails.append(parsed)

        return emails

    def idle_start(self, folder: str = "INBOX") -> None:
        if not self._client:
            raise RuntimeError("Not connected. Call connect() first.")
        self._client.select_folder(folder, readonly=True)
        self._client.idle()

    def idle_check(self, timeout: int = 30) -> list:
        if not self._client:
            raise RuntimeError("Not connected.")
        return self._client.idle_check(timeout=timeout)

    def idle_done(self) -> list:
        if not self._client:
            raise RuntimeError("Not connected.")
        return self._client.idle_done()

    def __enter__(self) -> EmailClient:
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.disconnect()
