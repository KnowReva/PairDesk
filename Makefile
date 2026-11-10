# PairDesk: everything is installed from files/ as laid out there.
PREFIX ?= /usr
DESTDIR ?=
PYTHON ?= python3

all:
	@echo "Nothing to build. Run: make install DESTDIR=..., make check, or make man"

install:
	mkdir -p $(DESTDIR)/
	cp -R files/. $(DESTDIR)/
	chmod 755 $(DESTDIR)/usr/bin/pairdesk $(DESTDIR)/usr/bin/pairdesk-remote $(DESTDIR)/usr/lib/pairdesk/pairdesk-knock \
	          $(DESTDIR)/usr/sbin/pairdesk-uninstall $(DESTDIR)/usr/libexec/pairdesk/root-helper

check:
	$(PYTHON) -W ignore -m unittest tests/test_core.py

man:
	pandoc -s -t man man/pairdesk.1.md -o man/pairdesk.1
	pandoc -s -t man man/pairdesk-uninstall.8.md -o man/pairdesk-uninstall.8
	pandoc -s -t man man/pairdesk-remote.1.md -o man/pairdesk-remote.1

.PHONY: all install check man
