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
- `jira` Python package (required for `-t` option)

Install

- From a checkout:

  `python3.11 -m pip install .`

Run

- Local file:

  `acklogparser -f <ack-submission-*.tar.gz>`

- From tracker (downloads latest submission, parses, and posts output as comment):

  `acklogparser -t HCL-1234`

- Process specific attachment (specify attachment name after colon):

  `acklogparser -t HCL-1234:attachment-name`

  Authentication: Set environment variables `JIRA_USER` (email) and `JIRA_TOKEN` (API token)

  To generate an API token:
  1. Go to https://id.atlassian.com/manage-profile/security/api-tokens
  2. Click "Create API token"
  3. Copy the token and set:
     ```
     export JIRA_USER="your.email@company.com"
     export JIRA_TOKEN="your-api-token"
     ```


Dependencies
------------
Currently this script makes use of the '`models.py`' module from `auto-cert-kit.git`.

It is therefore important that you add the location of the `auto-cert-kit.git/kit` to 
your python path in order to make sure the correct modules are loaded.
