.PHONY: test install uninstall

# The interpreter for tests and install. On a Mac it must be Homebrew's, 3.10 or
# later: `make install PYTHON="$(brew --prefix)/bin/python3"` if it isn't first on PATH.
PYTHON ?= python3

test:
	$(PYTHON) -m unittest discover -s tests

# uswitch's own install and uninstall (SPEC.md D9, D15): `sudo make install` on
# Linux, plain `make install` on a Mac.
install:
	$(PYTHON) ./uswitch install

uninstall:
	$(PYTHON) ./uswitch uninstall
