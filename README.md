ack-results-parser
==================

A collection of scripts used to parse the output of an ACK run.


acklogparser
------------

Prerequisites

- Python 3.11
- Network access to the configured repo URLs (used by the RPM comparison)
- `dnf repoquery` (preferred) or `repoquery` available on PATH
- `python-hwinfo` / `hwinfo` (required for printing BIOS/CPU/NIC/Storage summary)

Install

- From a checkout:

  `python3.11 -m pip install .`

Run

- `acklogparser -f <ack-submission-*.tar.gz>`


Dependencies
------------
Currently this script makes use of the '`models.py`' module from `auto-cert-kit.git`.

It is therefore important that you add the location of the `auto-cert-kit.git/kit` to 
your python path in order to make sure the correct modules are loaded.
