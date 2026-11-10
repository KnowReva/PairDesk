#!/usr/bin/env python3
"""Write the website's Help page from the app's own help topics, so the two never drift apart.

    tools/make-help-page.py SITE_DIR      (writes SITE_DIR/help/index.html)
"""
import ast
import html
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = open(os.path.join(ROOT, "files/usr/bin/pairdesk")).read()
i = src.index("HELP_TOPICS = [")
j = src.index("\n]\n", i) + 3
topics = ast.literal_eval(src[i + len("HELP_TOPICS = "):j].strip())
ITEM = re.compile(r"^(\d+\. |• )")


def block(lines):
    tag = "ol" if re.match(r"^\d+\. ", lines[0]) else "ul"
    return "<%s>%s</%s>" % (tag, "".join("<li>%s</li>" % ITEM.sub("", ln) for ln in lines if ln), tag)


def convert(text):
    text = text.replace("<tt>", "<code>").replace("</tt>", "</code>")
    out = []
    for para in text.split("\n\n"):
        lines = para.split("\n")
        if all(ITEM.match(ln) for ln in lines if ln):
            out.append(block(lines))
        elif lines[1:] and all(ITEM.match(ln) for ln in lines[1:] if ln):
            out.append("<p>%s</p>%s" % (lines[0], block(lines[1:])))
        else:
            out.append("<p>%s</p>" % "<br>".join(lines))
    return "\n".join(out)


toc = "".join('<li><a href="#%s">%s</a></li>' % (t, html.escape(title)) for t, title, _ in topics)
body = "".join('<section id="%s"><h2>%s</h2>\n%s</section>\n' % (t, html.escape(title), convert(text))
               for t, title, text in topics)
fonts = ("https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700"
         "&family=IBM+Plex+Mono:wght@500;600&display=swap")
page = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PairDesk help</title>
<meta name="description" content="How to set up PairDesk, pair computers, share USB devices and folders, fix problems and uninstall.">
<link rel="icon" href="../favicon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="%s" rel="stylesheet">
<link rel="stylesheet" href="../style.css">
</head>
<body>
<div class="wrap">
  <header class="top">
    <a class="brand" href="../"><img src="../pairdesk.svg" alt="" width="34" height="34">PairDesk</a>
    <nav aria-label="Main"><a href="../#install">Install</a><a href="./">Help</a><a href="../issues/">Report a problem</a></nav>
  </header>
  <main class="doc">
    <h1>Help</h1>
    <p>The same help is in the app: press <b>?</b> in PairDesk's title bar. <b>Check this computer</b> there finds most problems by itself.</p>
    <ul class="toc">%s</ul>
    %s
  </main>
  <footer><span>PairDesk is a <a href="https://knowreva.com">KnowReva</a> project.</span><a href="../">Home</a><span>MIT licence</span></footer>
</div>
</body>
</html>
""" % (fonts, toc, body)
os.makedirs(os.path.join(sys.argv[1], "help"), exist_ok=True)
with open(os.path.join(sys.argv[1], "help", "index.html"), "w") as f:
    f.write(page)
print("Wrote %s/help/index.html (%d topics)" % (sys.argv[1], len(topics)))
