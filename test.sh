#!/bin/sh

PROJECT_DIR=$(dirname "$(readlink -f "$0")")
PROJECT_DIR_WINDOWS=$(cygpath --windows "$PROJECT_DIR")

winpty calibre-customize -b "$PROJECT_DIR/calibre_plugins/moth" && \
  CALIBRE_DEVELOP_FROM="$PROJECT_DIR_WINDOWS\\calibre\\src" winpty calibre-debug -g