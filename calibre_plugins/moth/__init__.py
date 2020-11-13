"""Calibre plugin for retrieving metadata from moly.hu."""

from __future__ import annotations
from typing import Any, Dict, FrozenSet, Iterable, List, NamedTuple, \
                   Optional, Tuple, TYPE_CHECKING, cast

import datetime
import itertools
import queue
import re
import threading
import calibre.ebooks.metadata.book.base as books
import calibre.ebooks.metadata.sources.base as sources
import calibre.utils.browser as browsers
import calibre.utils.date as date
import calibre.utils.logging as logging
import calibre_plugins.moth.api as api
import calibre_plugins.moth.exceptions as exceptions
import calibre_plugins.moth.json as json
import calibre_plugins.moth.parsers as parsers


# Make pylama happy
if "_" not in dir():
    def _(string: str) -> str:
        return string

if TYPE_CHECKING:
    # pylint: disable=E1136
    Results = queue.Queue[books.Metadata]
else:
    Results = queue.Queue


class CoverUrl(NamedTuple):
    """Class holding data relevant for a cover URL."""

    book_id: int
    book_edition_id: int
    isbn: str
    url: str


class MothRequest(NamedTuple):
    """Structure which holds the data related to a single request."""

    logger: logging.Log
    api: api.Client
    authors: Optional[str]
    title: Optional[str]
    identifiers: Dict[str, str]
    results: Results
    metadatas: Dict[int, books.Metadata]
    cover_urls: Dict[int, CoverUrl]


class Moth(sources.Source):
    """Plugin for accessing the covers and metadata of moly.hu."""

    name: str = "Moth"
    author: str = "Szilveszter Ördög"

    version: Tuple[int, int, int] = (0, 9, 0)
    minimum_calibre_version: Tuple[int, int, int] = (5, 0, 0)
    description: str = _("Downloads metadata and covers from Moly.hu")

    capabilities: FrozenSet[str] = frozenset({
        "identify", "cover"
    })
    touched_fields: FrozenSet[str] = frozenset({
        "title", "authors", "comments", "languages", "pubdate", "publisher",
        "rating", "series", "series_index", "tags", "identifier:isbn",
        "identifier:moly", "identifier:moly-edition"
    })
    can_get_multiple_covers: bool = True

    options: Any = (
        sources.Option("api_key", "string", None,
                       _("The key used to access the API"),
                       _("The key can be obtained from moly.hu.")),
    )

    def __init__(self, *arguments: str, **keyword_arguments: str) -> None:
        """Initialize the metadata source."""
        super().__init__(*arguments, **keyword_arguments)

        # The base class assumes that we have a log member
        self.log: Optional[logging.Log] = None

    def _get_browser(self) -> browsers.Browser:
        return cast(browsers.Browser, self.browser)

    def _get_cloned_browser(self) -> browsers.Browser:
        return cast(browsers.Browser, self._get_browser().clone_browser())

    # pylint: disable=R0913
    def _create_request(self, logger: logging.Log, results: Results,
                        abort_event: threading.Event, title: Optional[str],
                        authors: Optional[str], identifiers: Dict[str, str],
                        timeout: int) -> MothRequest:
        return MothRequest(
            logger=logger,
            api=api.Client(logger, self._get_browser(), timeout, abort_event,
                           cast(Dict[str, str], self.prefs)["api_key"]),
            authors=authors,
            title=title,
            identifiers=identifiers,
            results=results,
            metadatas={},
            cover_urls={}
        )

    def _clone_request(self, request: MothRequest) -> MothRequest:
        return MothRequest(
            logger=request.logger,
            api=api.Client(request.logger, self._get_cloned_browser(),
                           request.api.timeout, request.api.abort_event,
                           request.api.api_key),
            authors=request.authors,
            title=request.title,
            identifiers=request.identifiers,
            results=request.results,
            metadatas=request.metadatas,
            cover_urls=request.cover_urls
        )

    @staticmethod
    def _get_names(book: json.JsonObject, key: str) -> List[str]:
        json_objects = book.get_list(key)
        if json_objects is None:
            return []

        return [json_object.get_str("name") for json_object in json_objects]

    @staticmethod
    def _get_book_edition_id(identifiers: Dict[str, str]) -> Optional[int]:
        book_edition_id = identifiers.get("moly-edition")
        if book_edition_id is None:
            return None
        return int(book_edition_id)

    def _get_search_queries(self, request: MothRequest) -> Iterable[str]:
        title_tokens = list(cast(Iterable[str],
                                 self.get_title_tokens(request.title)))
        authors_tokens = list(cast(Iterable[str],
                                   self.get_author_tokens(request.authors)))

        if title_tokens and authors_tokens:
            yield " ".join(title_tokens + authors_tokens)
        if title_tokens:
            yield " ".join(title_tokens)

        stripped_title_tokens = list(cast(
            Iterable[str],
            self.get_title_tokens(request.title, strip_subtitle=True)
        ))
        if stripped_title_tokens != title_tokens:
            if title_tokens and authors_tokens:
                yield " ".join(stripped_title_tokens + authors_tokens)
            if title_tokens:
                yield " ".join(stripped_title_tokens)

    def _search_book_ids(self, request: MothRequest) -> List[int]:
        for query in self._get_search_queries(request):
            matches = request.api.fetch_search_results(query)
            if matches:
                request.logger.debug("Found %i result(s) for query %s" %
                                     (len(matches), query))
                return [match.get_int("id") for match in matches]

        return []

    @staticmethod
    def _fix_comments(comments: str) -> str:
        return re.sub(r" *([\r\n]+) *", r"\1", comments)

    @staticmethod
    def _add_subseries(book_series: List[parsers.Series], comment: str) -> str:
        subseries = [
            "Subseries: %s [%f]" % (series.series, series.series_index)
            for series in book_series[1:]
        ]

        return "\r\n".join(subseries) + "\r\n" + comment

    @staticmethod
    def _is_language_tag(tag: str) -> bool:
        return tag.endswith(" nyelvű")

    @staticmethod
    def _get_language(request: MothRequest, tag: str) -> Optional[str]:
        language_tags = {"kínai nyelvű": "cn",
                         "német nyelvű": "de",
                         "angol nyelvű": "en",
                         "spanyol nyelvű": "es",
                         "francia nyelvű": "fr",
                         "görög nyelvű": "gr",
                         "magyar nyelvű": "hu",
                         "olasz nyelvű": "it",
                         "japán nyelvű": "jp",
                         "orosz nyelvű": "ru",
                         "török nyelvű": "tr"}
        language = language_tags.get(tag.lower().strip())
        if language is None and Moth._is_language_tag(tag):
            request.logger.debug("Unknown language %s" % tag)
        return language

    @staticmethod
    def _get_languages(request: MothRequest, tags: List[str]) -> List[str]:
        languages = [
            language for tag in tags
            if (language := Moth._get_language(request, tag)) is not None
        ]
        if not languages:
            languages.append("hu")

        return languages

    # pylint: disable=R0913
    def _add_metadata(self, request: MothRequest, book: json.JsonObject,
                      book_series: List[parsers.Series],
                      book_edition: json.JsonObject, relevance: int) -> None:
        book_id = book.get_int("id")
        book_edition_id = book_edition.get_int("id")

        request.logger.debug("Adding book with ID %i and edition ID %i" %
                             (book_id, book_edition_id))

        tags = self._get_names(book, "tags")

        metadata = books.Metadata(book.get_str("title"),
                                  self._get_names(book, "authors"))

        metadata.comments = self._add_subseries(
            book_series,
            self._fix_comments(book.get_str("description"))
        )
        metadata.languages = self._get_languages(request, tags)
        year = book_edition.get_optional_int("year")
        if year is not None:
            metadata.pubdate = cast(datetime.datetime,
                                    date.parse_only_date(str(year) + "-01-01"))
        metadata.publisher = book_edition.get_str("publisher")
        metadata.rating = float(book.get_float("like_average"))
        if len(book_series) > 0:
            metadata.series = book_series[0].series
            metadata.series_index = book_series[0].series_index
        metadata.tags = [tag for tag in tags if not self._is_language_tag(tag)]
        metadata.isbn = book_edition.get_str("isbn")
        metadata.set_identifier("moly", str(book_id))
        metadata.set_identifier("moly-edition", str(book_edition_id))

        cover_url = book_edition.get_optional_str("cover")
        if cover_url is not None:
            if metadata.isbn is not None:
                self.cache_isbn_to_identifier(metadata.isbn, book_edition_id)
            self.cache_identifier_to_cover_url(book_edition_id, cover_url)
            metadata.has_cached_cover_url = True

        metadata.source_relevance = relevance

        request.metadatas[relevance] = metadata

    # pylint: disable=R0913
    def _add_metadatas(self, request: MothRequest, book: json.JsonObject,
                       book_series: List[parsers.Series],
                       book_editions: List[json.JsonObject],
                       relevance: int) -> None:
        relevance_counter = itertools.count(relevance)

        for book_edition in book_editions:
            self._add_metadata(request, book, book_series, book_edition,
                               next(relevance_counter))

    def _fetch_and_add_metadata(self, request: MothRequest, book_id: int,
                                relevance: int) -> None:
        # Since this method is run in a separate thread, we need to clone the
        # request
        cloned_request = self._clone_request(request)

        book = cloned_request.api.fetch_book(book_id)
        if book is None:
            return

        book_series = cloned_request.api.fetch_book_series(book)
        book_editions = cloned_request.api.fetch_book_editions(book_id)

        self._add_metadatas(cloned_request, book, book_series, book_editions,
                            100 * relevance)

    def _fetch_and_add_metadatas(self, request: MothRequest,
                                 book_ids: List[int]) -> None:
        fetchers = [
            threading.Thread(
                target=self._fetch_and_add_metadata,
                args=(request, book_id, relevance)
            )
            for relevance, book_id in enumerate(book_ids)
        ]
        request.api.fetch_multiple(fetchers)

    @staticmethod
    def _match_identifiers_strict(book_edition_id: Optional[int],
                                  isbn: Optional[str],
                                  preferred_book_edition_id: Optional[int],
                                  preferred_isbn: Optional[str]) -> bool:
        return (preferred_book_edition_id is None or
                preferred_book_edition_id == book_edition_id) and \
               (preferred_isbn is None or preferred_isbn == isbn)

    @staticmethod
    def _filter_metadatas_strictly(request: MothRequest,
                                   preferred_book_edition_id: Optional[int],
                                   preferred_isbn: Optional[str]) -> \
            List[books.Metadata]:
        metadatas = []
        for _, metadata in sorted(request.metadatas.items()):
            identifiers = cast(Dict[str, str], metadata.get_identifiers())
            book_edition_id = Moth._get_book_edition_id(identifiers)
            isbn = identifiers.get("isbn")
            if Moth._match_identifiers_strict(book_edition_id, isbn,
                                              preferred_book_edition_id,
                                              preferred_isbn):
                metadatas.append(metadata)
        return metadatas

    @staticmethod
    def _match_identifiers(book_edition_id: Optional[int], isbn: Optional[str],
                           preferred_book_edition_id: Optional[int],
                           preferred_isbn: Optional[str]) -> bool:
        return (preferred_book_edition_id is None or
                preferred_book_edition_id == book_edition_id) or \
               (preferred_isbn is None or preferred_isbn == isbn)

    @staticmethod
    def _filter_metadatas(request: MothRequest,
                          preferred_book_edition_id: Optional[int],
                          preferred_isbn: Optional[str]) -> \
            List[books.Metadata]:
        metadatas = []
        for _, metadata in sorted(request.metadatas.items()):
            identifiers = cast(Dict[str, str], metadata.get_identifiers())
            book_edition_id = Moth._get_book_edition_id(identifiers)
            isbn = identifiers.get("isbn")
            if Moth._match_identifiers(book_edition_id, isbn,
                                       preferred_book_edition_id,
                                       preferred_isbn):
                metadatas.append(metadata)
        return metadatas

    @staticmethod
    def _list_metadatas(request: MothRequest) -> List[books.Metadata]:
        return [metadata for _, metadata in sorted(request.metadatas.items())]

    def _search_and_download_metadatas(self, request: MothRequest) -> \
            Optional[str]:
        """Download the metadatas associated with a book.

        :param title: The title of the book.
        :param authors: The authors of the book.
        :param identifiers: The known identifier's of the books.
        """
        request.logger.debug("Identifying books with title=%r, authors=%r, "
                             "identifiers=%r" %
                             (request.title, request.authors,
                              request.identifiers))

        # If the proper identifiers are present, we can download the metadata
        # directly instead of running a search
        book_ids = request.api.fetch_book_ids(request.identifiers)
        if not book_ids:
            # Try to search for the book
            book_ids = self._search_book_ids(request)
            if not book_ids:
                request.logger.error("No matches found")
                return _("No matches found")

        self._fetch_and_add_metadatas(request, book_ids)

        book_edition_id = self._get_book_edition_id(request.identifiers)
        isbn = request.identifiers.get("isbn")

        metadatas = self._filter_metadatas_strictly(request, book_edition_id,
                                                    isbn)
        if not metadatas:
            metadatas = self._filter_metadatas(request, book_edition_id, isbn)
        if not metadatas:
            metadatas = self._list_metadatas(request)

        for metadata in metadatas:
            request.results.put(metadata)

        return None

    @staticmethod
    def _get_big_cover_url(cover_url: str) -> str:
        return cover_url.replace("/normal/", "/big/")

    @staticmethod
    def _get_cover_urls(cover_url: str) -> List[str]:
        return [Moth._get_big_cover_url(cover_url), cover_url]

    def _download_cover(self, cover_url: str, request: MothRequest,
                        get_best_cover: bool) -> None:
        self.download_multiple_covers(request.title, request.authors,
                                      self._get_cover_urls(cover_url),
                                      get_best_cover, request.api.timeout,
                                      request.results, request.api.abort_event,
                                      request.logger, None)

    @staticmethod
    def _add_cover_url(request: MothRequest, book_id: int,
                       book_edition: json.JsonObject, cover_url: str,
                       relevance: int) -> None:
        request.cover_urls[relevance] = CoverUrl(
            book_id=book_id,
            book_edition_id=book_edition.get_int("id"),
            isbn=book_edition.get_str("isbn"),
            url=cover_url
        )

    @staticmethod
    def _add_cover_urls(request: MothRequest, book: json.JsonObject,
                        book_editions: List[json.JsonObject],
                        relevance: int) -> None:
        relevance_counter = itertools.count(relevance)

        book_id = book.get_int("id")
        book_cover_url = book.get_optional_str("cover")

        for book_edition in book_editions:
            cover_url = book_edition.get_optional_str("cover") or \
                        book_cover_url
            if cover_url is None:
                continue
            big_cover_url = Moth._get_big_cover_url(cover_url)

            Moth._add_cover_url(request, book_id, book_edition, big_cover_url,
                                next(relevance_counter))
            Moth._add_cover_url(request, book_id, book_edition, cover_url,
                                next(relevance_counter))

    def _fetch_and_add_cover_url(self, request: MothRequest, book_id: int,
                                 relevance: int) -> None:
        # Since this method is run in a separate thread, we need to clone the
        # request
        cloned_request = self._clone_request(request)

        book = cloned_request.api.fetch_book(book_id)
        if book is None:
            return

        book_editions = cloned_request.api.fetch_book_editions(book_id)

        Moth._add_cover_urls(cloned_request, book, book_editions,
                             100 * relevance)

    def _fetch_and_add_cover_urls(self, request: MothRequest,
                                  book_ids: List[int]) -> None:
        fetchers = [
            threading.Thread(
                target=self._fetch_and_add_cover_url,
                args=(request, book_id, relevance)
            )
            for relevance, book_id in enumerate(book_ids)
        ]
        request.api.fetch_multiple(fetchers)

    @staticmethod
    def _filter_cover_urls_strictly(request: MothRequest,
                                    preferred_book_edition_id: Optional[int],
                                    preferred_isbn: Optional[str]) -> \
            List[str]:
        cover_urls = []
        for _, cover_url in sorted(request.cover_urls.items()):
            if Moth._match_identifiers_strict(cover_url.book_edition_id,
                                              cover_url.isbn,
                                              preferred_book_edition_id,
                                              preferred_isbn):
                cover_urls.append(cover_url.url)
        return cover_urls

    @staticmethod
    def _filter_cover_urls(request: MothRequest,
                           preferred_book_edition_id: Optional[int],
                           preferred_isbn: Optional[str]) -> List[str]:
        cover_urls = []
        for _, cover_url in sorted(request.cover_urls.items()):
            if Moth._match_identifiers(cover_url.book_edition_id,
                                       cover_url.isbn,
                                       preferred_book_edition_id,
                                       preferred_isbn):
                cover_urls.append(cover_url.url)
        return cover_urls

    @staticmethod
    def _list_cover_urls(request: MothRequest) -> List[str]:
        return [
            cover_url.url
            for _, cover_url in sorted(request.cover_urls.items())
        ]

    def _search_and_download_covers(self, request: MothRequest,
                                    get_best_cover: bool) -> Optional[str]:
        request.logger.debug("Identifying book covers with title=%r, "
                             "authors=%r, identifiers=%r" %
                             (request.title, request.authors,
                              request.identifiers))

        # If the proper identifiers are present, we can download the metadata
        # directly instead of running a search
        book_ids = request.api.fetch_book_ids(request.identifiers)
        if not book_ids:
            # Try to search for the book
            book_ids = self._search_book_ids(request)
            if not book_ids:
                request.logger.error("No matches found")
                return _("No matches found")

        self._fetch_and_add_cover_urls(request, book_ids)

        book_edition_id = self._get_book_edition_id(request.identifiers)
        isbn = request.identifiers.get("isbn")

        cover_urls = self._filter_cover_urls_strictly(request, book_edition_id,
                                                      isbn)
        if not cover_urls:
            cover_urls = self._filter_cover_urls(request, book_edition_id,
                                                 isbn)
        if not cover_urls:
            cover_urls = self._list_cover_urls(request)
        if not cover_urls:
            return _("Could not find covers")

        self.download_multiple_covers(request.title, request.authors,
                                      cover_urls, get_best_cover,
                                      request.api.timeout,
                                      request.results, request.api.abort_event,
                                      request.logger, None)
        return None

    # API used by higher level

    def cli_main(self, args: List[str]) -> None:
        """Entry point for the plugins command line interface.

        :param args: Arguments from the command line
        """
        raise NotImplementedError("The %s plugin has no command line interface"
                                  % self.name)

    # pylint: disable=R0913
    def identify(self, log: logging.Log, result_queue: Results,
                 abort: threading.Event, title: str = None,
                 authors: str = None, identifiers: Dict[str, str] = None,
                 timeout: int = 30) -> Optional[str]:
        """Download a books metadata.

        :param log: Logger
        :param result_queue: The queue where the results should be put.
        :param abort: Signals whether the download should be aborted.
        :param title: The book's title.
        :param authors: The book's authors.
        :param identifiers: List of known book identifiers.
        :param timeout: The timeout for the download in seconds.
        """
        self.log = log

        request = self._create_request(log, result_queue, abort, title,
                                       authors, identifiers or {}, timeout)
        try:
            return self._search_and_download_metadatas(request)
        except exceptions.Aborted:
            return _("Aborted")

    @staticmethod
    def get_book_url(identifiers: Dict[str, str]) -> \
            Optional[Tuple[str, str, str]]:
        """Get the book's URL.

        :param identifiers: List of known book identifiers.
        """
        book_id = identifiers.get("moly", None)
        if book_id is None:
            return None
        return ("moly", book_id, "https://moly.hu/konyvek/%s" % book_id)

    @staticmethod
    def get_book_url_name(idtype: str, idval: str, url: str) -> str:
        """Return a human readable name for the book URL.

        :param identifier: The name of the identifier
        :param value: The value of the identifier
        :param url: The URL associated with the identifier's value
        """
        return "moly.hu"

    def get_cached_cover_url(self, identifiers: Dict[str, str]) -> \
            Optional[str]:
        """Return cached cover URL for the book identified by the identifiers.

        :param identifiers: List of known book identifiers
        """
        moly_edition_id = identifiers.get("moly-edition")
        if moly_edition_id is None:
            isbn = identifiers.get("isbn", None)
            if isbn is not None:
                moly_edition_id_int = cast(
                    Optional[int], self.cached_isbn_to_identifier(isbn)
                )
                if moly_edition_id_int is not None:
                    moly_edition_id = str(moly_edition_id_int)

        if moly_edition_id is not None:
            return cast(str, self.cached_identifier_to_cover_url(
                int(moly_edition_id))
            )

        return None

    def download_cover(self, log: logging.Log, result_queue: Results,
                       abort: threading.Event, title: str = None,
                       authors: str = None, identifiers: Dict[str, str] = None,
                       timeout: int = 30, get_best_cover: bool = False) -> \
            Optional[str]:
        """Download a book cover using the supplied information.

        :param log: Logger
        :param result_queue: The queue where the results should be put.
        :param abort: Signals whether the download should be aborted.
        :param title: The book's title.
        :param authors: The book's authors.
        :param identifiers: List of known book identifiers.
        :param timeout: The timeout for the download in seconds.
        :param get_best_cover: Whether to download only the best matching
                               cover.
        """
        self.log = log

        request = self._create_request(log, result_queue, abort, title,
                                       authors, identifiers or {}, timeout)
        try:
            cover_url = self.get_cached_cover_url(identifiers or {})
            if cover_url is not None:
                self._download_cover(cover_url, request, get_best_cover)
                return None

            return self._search_and_download_covers(request, get_best_cover)
        except exceptions.Aborted:
            return _("Aborted")


if __name__ == "__main__":
    # To run these test use: calibre-debug -e __init__.py

    # bad author
    # good author
    # bad title
    # bad title, bad author
    # bad title, good author
    # good title
    # good title, bad author
    # good title, good author
    # bad isbn
    # bad isbn, bad author
    # bad isbn, good author
    # bad isbn, bad title
    # bad isbn, bad title, bad author
    # bad isbn, bad title, good author
    # bad isbn, good title
    # bad isbn, good title, bad author
    # bad isbn, good title, good author
    # good isbn
    # good isbn, bad author
    # good isbn, good author
    # good isbn, bad title
    # good isbn, bad title, bad author
    # good isbn, bad title, good author
    # good isbn, good title
    # good isbn, good title, bad author
    # good isbn, good title, good author
    # bad moly-id
    # bad moly-id, bad author
    # bad moly-id, good author
    # bad moly-id, bad title
    # bad moly-id, bad title, bad author
    # bad moly-id, bad title, good author
    # bad moly-id, good title
    # bad moly-id, good title, bad author
    # bad moly-id, good title, good author
    # bad moly-id, bad isbn
    # bad moly-id, bad isbn, bad author
    # bad moly-id, bad isbn, good author
    # bad moly-id, bad isbn, bad title
    # bad moly-id, bad isbn, bad title, bad author
    # bad moly-id, bad isbn, bad title, good author
    # bad moly-id, bad isbn, good title
    # bad moly-id, bad isbn, good title, bad author
    # bad moly-id, bad isbn, good title, good author
    # bad moly-id, good isbn
    # bad moly-id, good isbn, bad author
    # bad moly-id, good isbn, good author
    # bad moly-id, good isbn, bad title
    # bad moly-id, good isbn, bad title, bad author
    # bad moly-id, good isbn, bad title, good author
    # bad moly-id, good isbn, good title
    # bad moly-id, good isbn, good title, bad author
    # bad moly-id, good isbn, good title, good author
    # good moly-id
    # good moly-id, bad author
    # good moly-id, good author
    # good moly-id, bad title
    # good moly-id, bad title, bad author
    # good moly-id, bad title, good author
    # good moly-id, good title
    # good moly-id, good title, bad author
    # good moly-id, good title, good author
    # good moly-id, bad isbn
    # good moly-id, bad isbn, bad author
    # good moly-id, bad isbn, good author
    # good moly-id, bad isbn, bad title
    # good moly-id, bad isbn, bad title, bad author
    # good moly-id, bad isbn, bad title, good author
    # good moly-id, bad isbn, good title
    # good moly-id, bad isbn, good title, bad author
    # good moly-id, bad isbn, good title, good author
    # good moly-id, good isbn
    # good moly-id, good isbn, bad author
    # good moly-id, good isbn, good author
    # good moly-id, good isbn, bad title
    # good moly-id, good isbn, bad title, bad author
    # good moly-id, good isbn, bad title, good author
    # good moly-id, good isbn, good title
    # good moly-id, good isbn, good title, bad author
    # good moly-id, good isbn, good title, good author
    _logger: logging.Log = sources.create_log(open("test.log", "wb"))
    _results: Results = queue.Queue()
    _abort_event = threading.Event()
    _moth = Moth(plugin_path="")
    _moth.identify(_logger, _results, _abort_event)
    _moth.identify(_logger, _results, _abort_event,
                   identifiers={"moly": "1000000000"})
    _moth.identify(_logger, _results, _abort_event,
                   identifiers={"moly": "15331"})
    _moth.identify(_logger, _results, _abort_event,
                   identifiers={"moly": "15331", "moly-edition": "49578"})
#    moth.identify(log, results, abort_event,
#                  identifiers={"isbn": "963825419X"})
#    moth.identify(log, results, abort_event,
#                  title="slipszi")
#    moth.identify(log, results, abort_event,
#                  title="vándorünnep")
#    moth.identify(log, results, abort_event,
#                  title="vándorünnep", authors=["Ernest Hemingway"])
#    moth.identify(log, results, abort_event,
#                  title="vándorünnep", authors=["Ördög Szilveszter"])
#    moth.identify(log, results, abort_event,
#                  authors=["Ernest Hemingway"])
#    moth.identify(log, results, abort_event,
#                  identifiers={"moly": 322714})
