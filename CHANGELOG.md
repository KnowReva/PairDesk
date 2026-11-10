# Changelog

## 2.9.2 (2026-11-07)
- Fixed: pressing **Cancel** in the sign-out notice still signed the session out when the countdown ended. The
  notice and PairDesk's link kept their note in different folders, so the link never saw Cancel (and the
  connecting computer never saw "done", which made it wait). They now share one place.
- When someone presses Cancel, the connecting computer says so: "Sign-out refused on …".
- After **Sign out now**, the connecting computer goes on within a few seconds.

## 2.9.1 (2026-11-07)
- Fixed: pressing **Sign out now** in the notice on the other computer could leave the connecting computer
  waiting, then saying "You're signed in on the screen". The sign-out was started from inside the session being
  signed out and ended with it. PairDesk's link now does it (the notice only passes on Sign out now or Cancel),
  and the connecting computer checks the other one directly while it waits.

## 2.9.0 (2026-11-07)
- **Pairing works like a one-time password, with nothing shared between installs.** PairDesk no longer ships
  a pairing key that every install has. The computer you sit at knocks with a new random key for each pairing
  attempt (deleted afterwards). The other computer lets an unknown key in to the pairing gate only, and a code
  works only for the key that asked for it.
- **Pair over SSH, no code:** when the other computer accepts SSH logins, Add a remote computer offers this
  first. You sign in with its password (asked once, not saved) and PairDesk pairs by itself; nothing to do at
  its screen. A code is used only when SSH isn't available. Terminal: `pairdesk pair ADDRESS --ssh`.
- New setting: **Accept pairing requests only after I press "Pair a new computer"** (pairing over SSH still
  works).
- **Pairing codes are hidden until you press Show** (the field where you type a code has an eye to hide it).
- An older computer (2.8 or earlier) can still pair with this one; this one asks you to update an older one
  first (+ > Set it up over SSH does it).
- SECURITY.md: how to report a problem, and how pairing and the link keep a computer safe.

## 2.8.3 (2026-11-07)
- Fixed: in 2.8.2 the sent camera disappeared from every app. Making it visible to the Camera app also let the
  system reset who may use it; PairDesk now gives the access back right away, and its device rule runs after
  the system's own one.
- Connect > Options: choose the speakers the remote sound plays on (or the remote computer's own), the
  microphone that's sent, and echo and room-noise reduction, besides the desktop toolbar's Sound menu.

## 2.8.2 (2026-11-07)
- **Sound menu** in the desktop toolbar: play the remote computer's sound here (pick speakers or headset), on
  its own speakers, or not at all; choose which microphone is sent (e.g. the headset instead of the built-in
  one), or none; optional echo and room-noise reduction. Changes reopen the desktop view for a moment; the
  session stays. Remembered for each computer.
- Fixed: a camera sent as video worked in Teams and browsers but not in the Camera app (apps that find
  cameras through PipeWire). It's announced again once video flows.

## 2.8.1 (2026-11-07)
- Camera menu: a camera that's lent to the other computer as a USB device is listed with "take it back and
  send as video" (before, the menu said "No camera on this computer").

## 2.8.0 (2026-11-07)
- **The remote desktop opens in PairDesk's own window, with a toolbar** (like Remmina): USB devices (send
  yours, use theirs, also devices plugged in during the session), camera, folders, the PairDesk window,
  minimize, full screen and Disconnect. In full screen the toolbar drops down when the mouse touches the top
  edge. Settings > Desktop window switches back to the viewer's own window.
- **Cameras as video:** Toolbar > Camera sends a camera of this computer as compressed video; on the remote
  computer it's a normal camera called "PairDesk camera" that Teams, Zoom, browsers and camera apps can use.
  Much smoother than sending the camera as a USB device. What's missing (ffmpeg here, the virtual camera
  there) is offered and installed over SSH.
- The remote session starts at the full size of the screen and is fitted to the window, so going full
  screen works with desktop servers that can't enlarge a session (xrdp).
- Fixed: "usbip: error: tcp connect" when disconnecting (a device was being sent again while it was taken back).
- USB device rows for webcams point to the smoother camera option.

## 2.7.10 (2026-11-07)
- **Webcams, security keys and other devices sent from another computer now work in a remote desktop
  session** (GNOME Remote Login, xrdp). Linux gives devices only to whoever sits at the computer's own
  screen; a remote session has none, so apps said "No camera found". USB setup now adds a rule that gives
  PairDesk's users access to exactly the devices that arrived from a paired computer. Upgrading adds it
  where USB sharing is already set up.

## 2.7.9 (2026-11-07)
- Fixed: the update window on the other computer failed with "No such file or directory" (and a system
  problem report) when the update had already been installed over SSH. It now notices and says so.
- Upgrading PairDesk reloads systemd and restarts the USB services that setup turned on, also when they
  had stopped (no more "unit file changed on disk" warnings).

## 2.7.8 (2026-11-07)
- Fixed: a USB device that couldn't be sent showed the same error again and again
  ("usbip: error: Attach Request … Unknown Op Common Status"). PairDesk now says once what's wrong (for
  example, this computer's USB service isn't running) and tries again only when you flip its switch.
- USB devices are only sent when this computer's USB service is actually running, not just set up.

## 2.7.7 (2026-11-07)
- `pairdesk setup` and `pairdesk install NAME` in a terminal (also "Install over SSH") show each step and
  apt's progress, instead of looking stuck while they work.
- USB setup no longer tries to install the classic sudo package where sudo-rs is used (Ubuntu 26.04).
- A service that doesn't start no longer holds up setup for ever (2-minute limit).

## 2.7.6 (2026-11-07)
- Fixed: turning remote access off switched the desktop server off too (after USB setup), and turning it
  on again couldn't restore GNOME Remote Login, so the other computer said "no desktop server running".
  Remote access now only opens and closes PairDesk's port; the desktop server stays as you set it up.
- Fixed: installing an add-on from a terminal or over SSH (`pairdesk install NAME`, "Install over SSH")
  couldn't ask for the admin password, so nothing was installed and the question came back every time.
  It now asks with sudo in the terminal. PairDesk checks afterwards and says if something is still missing.
- Skipping an add-on for the other computer is remembered until your options change.

## 2.7.5 (2026-11-07)
- Fixed: "Installing an update needs an administrator's password (pkexec)" on Ubuntu 26.04, whose pkexec
  (polkit 127) also sets SUDO_UID. Installing still never works through the USB setup's sudo rule.
- Updating an older remote computer: **Install now over SSH** before signing in, or approve it in its
  window after signing in.
- **Add-ons the other computer needs** for your options (folder sharing, USB sharing) are listed before
  signing in and installed there over SSH in a terminal.
- The bar at the top of the remote desktop also shows in a window, so you can go back to full screen or
  minimize.
- Options are saved for each computer as you change them (now confirmed on screen), and newly paired
  computers start with your latest options.

## 2.7.4 (2026-11-07)
- **Updates come first.** When you connect and one computer has a newer PairDesk, PairDesk asks before
  signing in: *Update first* or *Skip and connect*. When nobody is signed in on the other computer's screen,
  a terminal installs it there over SSH, then PairDesk connects.
- Fixed: "'NoneType' object has no attribute 'wait'" when disconnecting.
- Fixed: the update notice could go to a session that had already ended, so it never appeared.

## 2.7.3 (2026-11-07)
- Fixed: "Could not connect: remote port forwarding failed" when an earlier connection to the same computer was
  still open (another PairDesk window, or one left running across an update). The newest connection now ends
  the earlier one properly, and the older window stops instead of reconnecting. The computer you connect to
  also drops a connection whose computer went away within 30 seconds.

## 2.7.2 (2026-11-07)
- **Paired computers are remembered by IP address.** A name you type (office-pc.local) is turned into its IP
  before pairing, and kept too. Computers paired by name switch to their IP the next time you connect.
- If a computer's IP address changes (the router gave it a new one), PairDesk finds it again by its name or
  on the network, and moves only if the computer there has the same key it had at pairing.

## 2.7.1 (2026-11-07)
- Fixed: "You're signed in on the screen" when nobody was. A GNOME Remote Login session left from an earlier
  connection counted as signed in on the screen; now only sessions on the computer's own screen count.

## 2.7.0 (2026-11-07)
- **Paired computers update each other.** After connecting, if one computer has a newer PairDesk, PairDesk
  offers to update the other one: *Update this computer* copies the newer version from the remote computer,
  *Send update* sends yours to it. Nothing is installed without an administrator's password on the computer
  being updated, even after USB setup. With nobody at the remote screen, it waits there; install it over SSH
  with `pairdesk update --install`, or PairDesk offers it the next time it's opened there.
  Terminal: `pairdesk update NAME`. Works between computers that both have 2.7.0 or later; an older
  computer needs updating once by hand or with *Set it up over SSH*.
- **GNOME on Wayland only (GNOME 49+, Ubuntu 25.10 and later):** xrdp can't start a desktop there
  ("Error connecting to user session"). PairDesk now says so and switches to GNOME Remote Login, in the app
  or over SSH with `pairdesk desktop gnome`. A computer without a monitor is set up with GNOME Remote
  Login when it has no X11 GNOME.
- Fixed: the 30-second sign-out notice didn't appear on the remote screen (the link couldn't find the
  desktop session), so the session was signed out without warning.

## 2.6.1 (2026-11-07)
- New KnowReva colours, graphite and azure, and a new icon (two overlapping screens). Azure is the default accent colour (a colour you chose stays).
- The app itself uses the graphite and azure look in light and dark. Settings > Appearance > Style switches back to
  your system theme.
- Examples and screenshots use generic names (office-pc, laptop, 192.168.1.20); wording updated for the new
  Add a remote computer wizard.

## 2.6.0 (2026-11-07)
- **Add a remote computer is now a step-by-step wizard.** PairDesk lists the computers on your network that
  accept connections (no add-on needed), or you type just the IP. The user name and port are filled in by
  the remote computer itself. Every problem screen says what to do next.
- **Set it up over SSH:** when PairDesk doesn't answer at an address but SSH does, the wizard installs or
  updates PairDesk there (never downgrades), turns remote access on and runs the guided setup.
- **Get the code over SSH:** pair without seeing the other screen. The SSH password is asked in PairDesk's
  own window once per session (one shared connection for 10 minutes) and is never saved.
- `pairdesk pair 192.168.1.20` and `pairdesk scan` work with plain IPs; addresses are shown as plain IPs.
- Every dialog: the main button is highlighted, risky ones (Unpair, Uninstall, Sign out) are red, Enter
  does the main action or the safe choice; Copy buttons confirm; disabled buttons say why.
- Fixed: pressing Enter in the old Add dialog closed it without doing anything; the selected computer's
  name was hard to read; a removed (not purged) PairDesk counted as installed.

## 2.5.0 (2026-11-07)
- New name: **PairDesk** (was DeskBridge). The command is `pairdesk`, the package `pairdesk`.
- Upgrading from DeskBridge keeps your settings and pairings: paired computers keep working without pairing again,
  and a computer without a monitor keeps accepting connections. USB sharing needs "Set up USB sharing" once more.
- Website: https://pairdesk.knowreva.com

## 2.4.0 (2026-11-07)
- Welcome screen on first start: one question, then straight to the next step.
- Help > Check this computer (and `pairdesk doctor`): finds problems and says how to fix each.
- New icon; "A KnowReva project"; About shows the website.
- Store-ready packaging: AppStream metadata, man pages, Debian source package.

## 2.3.1 (2026-11-07)
- "pairdesk pairing" and "headless-setup" say exactly what to do next: the address and code to type on the other computer, then how to connect.
- Pairing again with an already-paired computer is reported as paired.
- No-sleep at the sign-in screen uses a system dconf setting (no more dconf warnings); removed on uninstall.
- sudo prompts name the user whose password is asked.

## 2.3.0 (2026-11-07)
- Help menu: Help topics, set up a computer without a monitor over SSH, log file, About.
- New "pairdesk headless-setup" command for computers without a monitor.

## 2.2.0 (2026-11-07)
- Progress window for installs, setup and uninstall.
- If the same user is signed in on the remote screen (xrdp), PairDesk can sign that session out after a 30-second notice there (Cancel possible).
- Computers without a screen: pairing requests stay open; read the code over SSH with "pairdesk pairing".
- Terminal output is live when run over SSH.

## 2.1.3 (2026-11-07)
- Progress window for add-on installs, setup, undo setup, the xrdp install and uninstall: current step, a progress bar from apt's own reports, live details, and a clear done/failed result.

## 2.1.2 (2026-11-07)
- Unpair on the computer others connect to: the other computer now removes the pairing by itself when PairDesk starts or tries to connect there.
- Open PairDesk windows update their lists when a pairing changes.
- New "pairdesk unpair NAME".

## 2.1.1 (2026-11-07)
- Fix: installing xrdp now also installs xorgxrdp (its display part); without it xrdp shows "Error connecting to user session".
- The remote computer reports desktop problems (xorgxrdp missing, same user signed in on its screen) in plain words, with the right Set up button.
- Switching between GNOME Remote Login and xrdp turns the other one off.
- Floating bar in full screen with minimize, restore and close.

## 2.1.0 (2026-11-07)
- Minimal layout: Connect and + next to the computer name, options folded with a summary, short status; details moved to Settings.
- Paired computers list with direction and Unpair (removes the pairing on both computers).
- Request pairing from the computer you connect from: the code pops up on the other computer, even when PairDesk is closed there.

## 2.0.0 (2026-11-07)
- PairDesk link: its own hardened encrypted service, run as the user on its own port (default 4722), open only while remote access is on, with its own keys and allowed-computer list; the system SSH login is no longer used or changed.
- Pairing with a one-time code and mutual proof instead of a password.
- Folder sharing back through a private socket (no SSH server needed on the computer you connect from).
- Optional direct desktop stream with a pinned certificate.
- Pairings from 1.x must be made again once.

## 1.6.0 (2026-11-07)
- Client/Server/Both tabs replaced by one "Let remote computers connect" switch and a status indicator in the title bar.
- Closing the window while connected minimizes it; the session ends with Disconnect or when the remote desktop window closes.
- A refused connection now closes its link at once (no stale links during automatic reconnects).
- Descriptions no longer compare PairDesk with other software.

## 1.5.0 (2026-11-07)
- Fits any screen: window sized to the screen, pages scroll, the Connect bar stays visible, options and USB lists reflow on narrow windows.
- Modern card-style look; Settings > Appearance: theme (system, light, dark) and accent colour. Dark mode works with any GTK theme.

## 1.4.2 (2026-11-07)
- Switching Client/Server/Both never shows dialogs or asks for a password. Missing pieces get buttons on the Current computer tab: Install SSH server, Set up desktop sharing, Set up USB sharing.
- "pairdesk mode" prints what is left to do instead of asking.

## 1.4.1 (2026-11-07)
- Server/Both no longer ask for the admin password when a desktop server is already running and setup was skipped.
- Admin prompt wording no longer mentions USB.
- Add-ons list shows what is on the computer and marks what PairDesk added.
- Addresses skip Docker/VM bridges; firewall option greyed out without setup.

## 1.4.0 (2026-11-07)
- Only Python, GTK and the SSH client are required. The viewer, folder sharing, SSH server, USB sharing and other add-ons are installed when first needed, after an explanation of what each is for and which side.
- New pairdesk-uninstall and "Uninstall PairDesk..." remove PairDesk and only the packages it installed.
- Recognize Debian's freerdp-x11; ask FreeRDP for its version.

## 1.3.0 (2026-11-07)
- Server/Both turn on without a desktop server; desktop sharing is a separate, explained step: set it up for me, show me how, or later.
- One-click xrdp install on non-GNOME desktops.
- Desktop login prompt says which password to use.

## 1.2.0 (2026-11-07)
- Setup is optional: desktop, sound, clipboard, printers and folders work without it; USB sharing is switched off with a clear note until set up.
- New "Undo setup" (Settings, or pairdesk unsetup).
- Server mode can use a desktop server that is already running.

## 1.1.2 (2026-11-07)
- Clearer wording: "remote computer" and "current computer" throughout.

## 1.1.1 (2026-11-07)
- Fix touchscreens and other HID devices shown as security keys; detect FIDO keys and touchscreens from their HID report descriptors.

## 1.1.0 (2026-11-07)
- Auto-reconnect after network drops, restoring USB devices and folders.
- Wake-on-LAN; keep the server awake during sessions.
- Picture quality presets, printer sharing, app-menu and login shortcuts.
- Keyboard/mouse warning, faster connect, CPU-tuned encryption, log file.

## 1.0.0 (2026-11-07)
- First release.

