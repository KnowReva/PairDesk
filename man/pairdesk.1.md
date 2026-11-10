---
title: PAIRDESK
section: 1
header: User Commands
footer: PairDesk 2.9.2
date: November 2026
---

# NAME

pairdesk - use another computer's desktop with your USB devices, folders and sound

# SYNOPSIS

**pairdesk** [*COMMAND* [*ARGUMENTS*]]

# DESCRIPTION

Without a command, **pairdesk** opens the app. PairDesk connects Linux computers on the
same network: full remote desktop with clipboard, sound and microphone, USB devices (security
keys, webcams, drives) in both directions, and shared folders in both directions.

The same package goes on every computer. A computer *accepts connections* only while remote
access is on, and only from computers paired with a one-time code. Connections use PairDesk's
own encrypted link on its own port (default 4722).

# COMMANDS

**access on**|**off**
: Let paired computers connect, or stop that and close PairDesk's port.

**pairing**
: On a computer without a screen (over SSH): show the code a pairing request is waiting for, or
  make a new one, explain what to type on the other computer, and wait until it is used.

**pair** *ADDRESS* [*CODE*]
: Pair with a remote computer. *ADDRESS* can be just its IP (**192.168.1.20**), a computer name
  (**office-pc.local**), or **user@host:port**; the user name and port are filled in from the remote
  computer. Without *CODE* it asks the remote computer to show one, then asks you to type it.

**pair** *ADDRESS* **--ssh** [*LOGIN*]
: Pair by signing in to the remote computer over SSH (its password; *LOGIN* defaults to the user and
  address of the remote computer): PairDesk gets a one-time code there and pairs with it, with no code
  to type. Works for a computer without a screen, or one that accepts pairing only after "Pair a new
  computer".

**pair-code** [**--plain**] [**--turn-on**]
: Show a one-time code so another computer can pair. **--plain** prints only the code, and
  **--turn-on** turns remote access on first (used by "Pair over SSH").

**headless-setup** [**--from-client** *NAME*]
: Guided setup of a computer without a monitor, run on that computer over SSH. Each step is checked
  first and you are asked before anything changes. "Set it up over SSH" in the app runs it with
  **--from-client** after installing or updating PairDesk there if needed.

**doctor**
: Check the current computer and say how to fix anything missing. Changes nothing. Exits with 1
  when something needs fixing.

**list**
: Paired computers, in both directions.

**unpair** *NAME*
: Remove a pairing on both computers.

**update** *NAME*
: Compare PairDesk on this computer and *NAME* and, after asking, update whichever is older:
  copy the newer package from *NAME* and install it here (sudo asks for your password), or send
  this computer's package to *NAME*, where an administrator must approve it.

**update --install**
: Install the update a paired computer sent here (e.g. over SSH on a computer without a screen).

**desktop gnome**|**xrdp**
: Choose the desktop server. GNOME on Wayland only (GNOME 49 and later, Ubuntu 25.10 and later)
  needs GNOME Remote Login, because xrdp can't start a desktop there. Asks for the Remote Login
  user name and password.

**connect** *NAME* [**--no-desktop**]
: Connect from the terminal.

**usb**
: USB devices on the current computer.

**send** *NAME* *KEY*|**off**, **take** *NAME* *KEY*|**off**
: Choose a USB device to send to *NAME*, or one of *NAME*'s devices to use here, on connect.

**port** *N*
: Use port *N* for PairDesk's link.

**scan**
: Find computers on the local network that accept connections (no add-on needed).

**setup**, **unsetup**
: Optional: set up or undo USB sharing and the network rule (asks for the admin password).

**features**, **install** *NAME*
: Show the optional add-ons, or install one.

# FILES

*~/.config/pairdesk/*
: Settings, keys and the link service's configuration.

*~/PairDesk Shared/*
: The folder shared with paired computers (can be changed in Settings).

*~/PairDesk/NAME/*
: Where a remote computer's shared folder appears.

*~/.cache/pairdesk/updates/*
: A PairDesk package a paired computer sent, until it's installed.

# SEE ALSO

**pairdesk-uninstall**(8), **xfreerdp**(1), **usbip**(8)

Website: https://pairdesk.knowreva.com

# AUTHOR

KnowReva.
