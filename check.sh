#!/bin/sh

if [ ! -d .mypy_stubs ]; then
  stubgen -m mechanize -m mechanize._mechanize -o .mypy_stubs || exit $?
fi

pylama calibre_plugins || exit $?
mypy calibre_plugins || exit $?
