"""Helper classes for parsing the downloaded data."""

from typing import List, NamedTuple, Optional, Tuple

import re
import html.parser
import calibre.utils.logging as logging


class Series(NamedTuple):
    """Class for holding series data."""

    series: str
    series_index: float
    url: str


class SeriesParser(html.parser.HTMLParser):
    """Class for parsing HTML to find series data."""

    def __init__(self, logger: logging.Log) -> None:
        """Intialize the parser.

        :param logger: The logger used for error reporting.
        """
        html.parser.HTMLParser.__init__(self)
        self._logger: logging.Log = logger
        self._in_series: bool = False
        self._text: str = ""
        self._url: str
        self._series: List[Series] = []

    def parse(self, content: str) -> List[Series]:
        """Parse the content.

        :param content: The content to parse.
        """
        self.feed(content)
        self.close()

        return self._series

    def error(self, message: str) -> None:
        """Handle an error.

        :param message: The error message.
        """
        self._logger.error(message)

    def handle_starttag(self, tag: str,
                        attrs: List[Tuple[str, Optional[str]]]) -> None:
        """Handle the starting tag of a HTML element.

        :param tag: The tag's name.
        :param attrs: The attributes of the tag.
        """
        if tag == "a":
            href = self._attribute(attrs, "href")
            if href is not None and href.startswith("/sorozatok/"):
                self._in_series = True
                self._text = ""
                self._url = href

    def handle_endtag(self, tag: str) -> None:
        """Handle the ending tag of a HTML element.

        :param tag: The tag's name.
        """
        if tag == "a" and self._in_series:
            self._in_series = False
            self._add_series(self._text, self._url)

    def handle_data(self, data: str) -> None:
        """Handle text nodes.

        :param data: The text.
        """
        if self._in_series:
            self._text += data

    def handle_entityref(self, name: str) -> None:
        """Handle entity references.

        :param string name: The name of the entity reference.
        """
        if self._in_series:
            self._text += html.unescape(name)

    def handle_charref(self, name: str) -> None:
        """Handle character references.

        :param string name: The content of the character reference.
        """
        if self._in_series:
            self._text += chr(int(name[1:-1]))

    def _add_series(self, text: str, url: str) -> None:
        if text.startswith("(") and text.endswith(")"):
            text = text[1:-1]

        match = re.match(r"^(.*) ([\d.,]*\d)\.?$", text)
        if match:
            series_name = match.group(1)
            series_index = self._parse_series_index(match.group(2))
        else:
            series_name = text
            series_index = 0.0

        for index, series in enumerate(self._series):
            if series.url == url:
                if series.series_index == 0.0 and series_index != 0.0:
                    self._series[index] = series._replace(
                        series_index=series_index
                    )
                return

        self._series.append(Series(
            series=series_name,
            series_index=series_index,
            url=url
        ))

    @staticmethod
    def _attribute(attributes: List[Tuple[str, Optional[str]]],
                   attribute: str) -> Optional[str]:
        for (name, value) in attributes:
            if name == attribute:
                return value
        return None

    @staticmethod
    def _parse_series_index(series_index: str) -> float:
        if series_index.endswith("."):
            series_index = series_index[:-1]
        series_index = series_index.replace(",", ".")
        return float(series_index)
