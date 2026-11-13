---
title: PAIRDESK-UNINSTALL
section: 8
header: System Administration
footer: PairDesk 2.9.5
date: November 2026
---

# NAME

pairdesk-uninstall - remove PairDesk and only the add-ons it installed

# SYNOPSIS

**pairdesk-uninstall** [**--dry-run**] [**--yes**] [**--keep-ssh-server**] [**--remove-settings**|**--keep-settings**] [**--remove-tools**]

# DESCRIPTION

Gives shared USB devices back, undoes setup, removes PairDesk and the add-ons PairDesk
installed, and keeps packages you installed yourself or that other software needs. Files in the
shared folder are kept. Run it as root, or from Settings > Uninstall PairDesk.

# OPTIONS

**--dry-run**
: Show what would be removed, change nothing.

**--yes**
: Don't ask before removing.

**--keep-ssh-server**
: Keep openssh-server even if PairDesk installed it.

**--remove-settings**, **--keep-settings**
: Remove or keep each user's PairDesk settings and keys (default: ask).

**--remove-tools**
: Also remove PairDesk's helper programs (viewer, sshfs, usbip, avahi-utils, xrdp…) that
  apt records as installed by you, when nothing else uses them. Without it, each is asked about.

# SEE ALSO

**pairdesk**(1)
