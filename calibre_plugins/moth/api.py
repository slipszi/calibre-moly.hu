"""Class for accessing the moly.hu API."""

from __future__ import annotations
from typing import Dict, List, Optional, cast

import threading
import time
import urllib
import urllib.error
import mechanize
import calibre.utils.browser as browsers
import calibre.utils.logging as logging
import calibre_plugins.moth.exceptions as exceptions
import calibre_plugins.moth.json as json
import calibre_plugins.moth.parsers as parsers


class Client:
    """Class for accessing the moly.hu API."""

    API_URL: str = "https://moly.hu/api/"

    # pylint: disable=R0913,E1136
    def __init__(self, logger: logging.Log, browser: browsers.Browser,
                 timeout: int, abort_event: threading.Event, api_key: str) -> \
            None:
        """Intialize the client.

        :param log: The logger.
        :param browser: The browser used for downlading data.
        :param timeout: The communication timeout.
        :param abort_event: The variable indicating whether the communication
                            should be aborted.
        :param api_key: The API key used to access the server.
        """
        self._logger: logging.Log = logger
        self._browser: browsers.Browser = browser
        self.timeout: int = timeout
        self.abort_event: threading.Event = abort_event
        self.api_key: str = api_key

    def _get_url(self, path: str, parameter: str = None) -> str:
        url = self.API_URL + path + "?key=" + self.api_key
        if parameter is not None:
            url += "&" + parameter
        return url

    @staticmethod
    def _encode_url(url: str) -> str:
        return urllib.parse.quote(url)

    def _fetch(self, url: str) -> Optional[json.SupportsReadBytes]:
        if self.abort_event.is_set():
            raise exceptions.Aborted()

        try:
            return cast(json.SupportsReadBytes,
                        self._browser.open_novisit(url, timeout=self.timeout))
        except (mechanize.BrowserStateError, urllib.error.HTTPError):
            self._logger.exception("Could not fetch URL %r" % url)
            return None

    def fetch_multiple(self, fetchers: List[threading.Thread]) -> None:
        """Execute multiple requests in parallel.

        :param fetchers: The list of threads doing the actual fetching.
        """
        for fetcher in fetchers:
            fetcher.start()
            # Don't send all requests at the same time
            if self.abort_event.is_set():
                raise exceptions.Aborted()
            time.sleep(1)

        # Wait until the downloads complete
        while True:
            a_fetcher_is_alive = False
            for fetcher in fetchers:
                fetcher.join(0.01)
                if fetcher.is_alive():
                    a_fetcher_is_alive = True
            if not a_fetcher_is_alive:
                break

    def _fetch_json(self, url: str) -> Optional[json.JsonObject]:
        response = self._fetch(url)
        if response is None:
            return None
        return json.JsonObject.from_stream(response)

    def _get_book_id_by_isbn_url(self, isbn: str) -> str:
        return self._get_url("book_by_isbn.json", "q=%s" % isbn)

    def _fetch_book_id_by_isbn(self, isbn: str) -> Optional[int]:
        self._logger.debug("Searching for book with ISBN %s" % isbn)
        response = self._fetch_json(self._get_book_id_by_isbn_url(isbn))
        if response is None:
            return None
        return response.get_int("id")

    def _get_search_results_url(self, query: str) -> str:
        return self._get_url("books.json", "q=" + query)

    def fetch_search_results(self, query: str) -> List[json.JsonObject]:
        """Return the books found by executing the search.

        :param query: The search query
        """
        self._logger.debug("Searching for book with search query %s" % query)
        url = self._get_search_results_url(self._encode_url(query))
        response = self._fetch_json(url)
        if response is None:
            return []
        return response.get_list("books")

    def _get_book_url(self, book_id: int) -> str:
        return self._get_url("book/%i.json" % book_id)

    def fetch_book(self, book_id: int) -> Optional[json.JsonObject]:
        """Return a book's metadata.

        :param book_id: The ID of the book.
        """
        self._logger.debug("Searching for book with ID %i" % book_id)
        response = self._fetch_json(self._get_book_url(book_id))
        if response is None:
            return None
        return response.get_object("book")

    def _get_book_editions_url(self, book_id: int) -> str:
        return self._get_url("book_editions/%i.json" % book_id)

    def fetch_book_editions(self, book_id: int) -> List[json.JsonObject]:
        """Return a list of a book's editions.

        :param book_id: The ID of the book.
        """
        self._logger.debug("Searching for editions of book with ID %i" %
                           book_id)
        response = self._fetch_json(self._get_book_editions_url(book_id))
        if response is None:
            return []
        return response.get_list("editions")

    def fetch_book_ids(self, identifiers: Dict[str, str]) -> List[int]:
        """Return the list of book IDs.

        :param identifiers: The available identifiers of a book.
        """
        book_ids = []

        book_id = identifiers.get("moly")
        if book_id is not None:
            book_ids.append(int(book_id))

        isbn = identifiers.get("isbn")
        if isbn is not None:
            book_id_by_isbn = self._fetch_book_id_by_isbn(isbn)
            if book_id_by_isbn is not None and str(book_id_by_isbn) != book_id:
                book_ids.append(book_id_by_isbn)

        return book_ids

    def fetch_book_series(self, book: json.JsonObject) -> \
            List[parsers.Series]:
        """Return information about which series is this book a part of.

        :param book: The book's metadata.
        """
        url = book.get_str("url")
        if url is None:
            return []

        self._logger.debug("Downloading series info from %s" % url)
        response = self._fetch(url)
        if response is None:
            return []

        content = response.read().decode("utf-8")
        return parsers.SeriesParser(self._logger).parse(content)
