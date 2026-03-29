#!/usr/bin/env python3
"""
DR Nyheder C64 Server
---------------------
Holt Nachrichten von DR.dk und stellt sie fuer den C64 bereit.
- Alles in Grossbuchstaben (C64 uppercase-Modus)
- Daenische Sonderzeichen werden umgeschrieben
- Paginierung: nach jeder Seite warten
"""

import socket
import threading
import xml.etree.ElementTree as ET
import html
import re
import sys

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Bitte installieren: pip install requests beautifulsoup4")
    sys.exit(1)

PORT  = 6510
WIDTH = 40   # C64 Bildschirmbreite
LINES = 22   # Sichtbare Zeilen pro Seite (C64 hat 25, etwas Puffer lassen)

FEEDS = {
    "1": ("SENESTE NYT",     "https://www.dr.dk/nyheder/service/feeds/senestenyt"),
    "2": ("INDLAND",         "https://www.dr.dk/nyheder/service/feeds/indland"),
    "3": ("UDLAND",          "https://www.dr.dk/nyheder/service/feeds/udland"),
    "4": ("POLITIK",         "https://www.dr.dk/nyheder/service/feeds/politik"),
    "5": ("PENGE",           "https://www.dr.dk/nyheder/service/feeds/penge"),
    "6": ("VIDEN",           "https://www.dr.dk/nyheder/service/feeds/viden"),
    "7": ("KULTUR",          "https://www.dr.dk/nyheder/service/feeds/kultur"),
    "8": ("SPORTEN",         "https://www.dr.dk/nyheder/service/feeds/sporten"),
    "9": ("SYD/SOENDERJYLL", "https://www.dr.dk/nyheder/service/feeds/regionale/syd"),
}

HEADERS = {"User-Agent": "Mozilla/5.0 (C64 DR Nyheder Reader)"}


def c64(text):
    """Bereinigt Text fuer C64: Case-Inversion, keine Sonderzeichen."""
    if not text:
        return ""
    text = html.unescape(text)
    # Daenische + deutsche Sonderzeichen (groessenabhaengig)
    replacements = {
        "æ": "ae", "ø": "oe", "å": "aa",
        "Æ": "AE", "Ø": "OE", "Å": "AA",
        "ä": "ae", "ö": "oe", "ü": "ue",
        "Ä": "AE", "Ö": "OE", "Ü": "UE",
        "ß": "ss",
        "\u2019": "'", "\u2018": "'",
        "\u201c": '"', "\u201d": '"',
        "\u2013": "-", "\u2014": "-",
        "\u2026": "...", "\xa0": " ",
        "\u00e9": "e", "\u00e8": "e", "\u00ea": "e",
        "\u00e0": "a", "\u00e2": "a",
        "\u00f4": "o", "\u00f9": "u", "\u00fb": "u",
        "\u00e7": "c", "\u00ef": "i", "\u00ee": "i",
    }
    for orig, repl in replacements.items():
        text = text.replace(orig, repl)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"\s+", " ", text).strip()
    # C64 invertiert Gross-/Kleinschreibung
    return text.swapcase()


def wrap(text, width=WIDTH):
    """Bricht Text auf C64-Breite um, gibt Liste von Zeilen zurueck."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        if not word:
            continue
        if len(current) + len(word) + (1 if current else 0) <= width:
            current = current + (" " if current else "") + word
        else:
            if current:
                lines.append(current)
            # Langes Wort aufteilen
            while len(word) > width:
                lines.append(word[:width])
                word = word[width:]
            current = word
    if current:
        lines.append(current)
    return lines


def divider(char="-", width=WIDTH):
    return char * width + "\r\n"


# ------------------------------------------------------------------ Netzwerk

def send(conn, text):
    try:
        conn.sendall(text.encode("ascii", "ignore"))
    except Exception:
        pass


def send_line(conn, text=""):
    send(conn, text[:WIDTH] + "\r\n")


def send_wrapped(conn, text):
    for line in wrap(text):
        send_line(conn, line)


def recv_char(conn, timeout=300):
    try:
        conn.settimeout(timeout)
        data = conn.recv(1)
        if data:
            return data.decode("ascii", "ignore").upper().strip()
        return None
    except Exception:
        return None


def recv_line(conn, timeout=60, maxlen=4):
    """Liest eine Zeile (bis Enter), mit Echo."""
    inp = ""
    conn.settimeout(timeout)
    try:
        while True:
            c = conn.recv(1)
            if not c:
                return None
            ch = c.decode("ascii", "ignore")
            if ch in ("\r", "\n"):
                break
            if ch.isprintable():
                inp += ch
                send(conn, ch.upper())  # Echo in Grossbuchstaben
            if len(inp) >= maxlen:
                break
    except Exception:
        return None
    send(conn, "\r\n")
    return inp.strip().upper()


# ---------------------------------------------------------------- Paginierung

class Pager:
    """Seitenweise Ausgabe. Wartet nach LINES Zeilen auf Tastendruck."""

    def __init__(self, conn, lines_per_page=LINES):
        self.conn = conn
        self.lpp = lines_per_page
        self.count = 0
        self.aborted = False

    def line(self, text=""):
        if self.aborted:
            return
        send_line(self.conn, text)
        self.count += 1
        if self.count >= self.lpp - 1:  # Reserve 1 Zeile fuer Pause-Nachricht
            self._pause()

    def wrapped(self, text):
        for l in wrap(text):
            self.line(l)

    def raw(self, text):
        """Sendet raw (z.B. divider mit \r\n)."""
        if self.aborted:
            return
        # Zaehle Zeilenumbrueche
        newlines = text.count("\r\n") + text.count("\n")
        send(self.conn, text)
        self.count += max(newlines, 1) if text.strip() else 0
        if self.count >= self.lpp - 1:  # Reserve 1 Zeile fuer Pause-Nachricht
            self._pause()

    def _pause(self):
        self.count = 0
        send(self.conn, "--- MELLEMRUM=MERE  Q=STOP ---")
        key = recv_char(self.conn, timeout=120)
        if key == "Q" or key is None:
            self.aborted = True
        # Loesche die Pause-Nachricht durch Ueberschreiben mit Leerzeichen
        send(self.conn, "\r" + " " * 30 + "\r\n")

    def reset(self):
        self.count = 0
        self.aborted = False


# --------------------------------------------------------------- Datenabruf

def fetch_articles(feed_url):
    try:
        r = requests.get(feed_url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        articles = []
        for item in root.findall(".//item"):
            title = c64(item.findtext("title", ""))
            desc  = item.findtext("description", "") or ""
            desc  = re.sub(r"<[^>]+>", " ", desc)
            desc  = c64(desc)[:300]
            link  = (item.findtext("link", "") or "").strip()
            articles.append({"title": title, "teaser": desc, "link": link})
        return articles
    except Exception as e:
        return []


def fetch_full_article(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "html.parser")
        paragraphs = []

        # Versuche verschiedene Selektoren
        for selector in [
            lambda s: s.find("article"),
            lambda s: s.find("div", class_=re.compile("article__body|articleBody|body-text", re.I)),
            lambda s: s.find("div", class_=re.compile("content|richtext", re.I)),
        ]:
            container = selector(soup)
            if container:
                for tag in container.find_all(["p", "h2", "h3"]):
                    t = c64(tag.get_text())
                    if t and len(t) > 20:
                        paragraphs.append(t)
                if paragraphs:
                    break

        # Fallback: alle <p>
        if not paragraphs:
            for p in soup.find_all("p"):
                t = c64(p.get_text())
                if t and len(t) > 40:
                    paragraphs.append(t)

        # Duplikate entfernen (passiert manchmal beim Scrapen)
        seen = set()
        unique = []
        for p in paragraphs:
            if p not in seen:
                seen.add(p)
                unique.append(p)

        return unique if unique else ["(ARTIKELTEXT IKKE TILGAENGELIG)"]
    except Exception as e:
        return [f"(FEJL: {c64(str(e))})"]


# ----------------------------------------------------------------- Verbindung

def show_logo(conn):
    """Zeigt das DR Nyheder Logo und wartet auf Mellemrum."""
    # C64 Farbcodes (PETSCII)
    RED = "\x1c"      # Ctrl+\ = Rot
    WHITE = "\x05"    # Ctrl+E = Weiss
    
    send(conn, "\r\n\r\n")
    # DR Logo mit daenischer Flagge (rot mit weissem Kreuz)
    # Zentriert fuer 40 Zeichen Breite
    # Weisses Kreuz: vertikaler Strich rechts im D, horizontaler Strich in der Mitte
    send_line(conn)
    send_line(conn, RED + "             ####  ####")
    send_line(conn, RED + "             #  " + WHITE + " # "+ RED + "#   #   ")
    send_line(conn, RED + "             #   " + WHITE + "#" + RED + " #   #")
    send_line(conn, WHITE + "             #   " + WHITE + "# ####")
    send_line(conn, RED + "             # "+ WHITE +    "  # "+ RED +"#   #")
    send_line(conn, RED + "             #   " + WHITE + "#" + RED + " #   #")
    send_line(conn, RED + "             #   " + WHITE + "#" + RED + " #   #")
    send_line(conn, RED + "             ####  #   #")
    send_line(conn)
    send_line(conn)
    send(conn, WHITE + "\r\n")
    send_line(conn, "          NYHEDER FRA DR.DK")
    send_line(conn, "         C64 ULTIMATE UDGAVE")
    send_line(conn)
    send_line(conn)
    send_line(conn)
    send(conn, "\r\n\r\n")
    send_line(conn, "     TRYK MELLEMRUM FOR AT STARTE")
    send(conn, "\r\n")
    
    # Warte auf Mellemrum (Space)
    while True:
        key = recv_char(conn, timeout=300)
        if key is None:
            return False
        if key == " " or key == "":
            break
    return True


def handle_client(conn, addr, log_callback=None):
    def log(msg):
        if log_callback:
            log_callback(msg)
        else:
            print(msg)

    log(f"FORBINDELSE: {addr[0]}:{addr[1]}")

    try:
        # Zeige Logo
        if not show_logo(conn):
            return
        
        # Clear screen und zeige Header
        send(conn, "\r\n")
        send(conn, divider("*"))
        send_line(conn, "   DR NYHEDER - C64 UDGAVE")
        send_line(conn, "   NYHEDER FRA DR.DK")
        send(conn, divider("*"))
        send(conn, "\r\n")

        while True:
            # Hauptmenue
            send_line(conn, c64("Vaelg kategori:"))
            send(conn, "\r\n")
            for key, (name, _) in FEEDS.items():
                send_line(conn, f"  {key}) {c64(name)}")
            send(conn, "\r\n")
            send_line(conn, c64("  Q) Afslut"))
            send(conn, "\r\n")
            send(conn, "DIT VALG: ")

            choice = recv_char(conn)
            send(conn, "\r\n")

            if choice is None or choice == "Q":
                send(conn, "\r\n")
                send_line(conn, "FARVEL!")
                break

            if choice not in FEEDS:
                send_line(conn, "UGYLDIGT VALG.")
                continue

            feed_name, feed_url = FEEDS[choice]
            send(conn, divider())
            send_line(conn, f"HENTER {feed_name}...")
            send(conn, divider())

            articles = fetch_articles(feed_url)
            if not articles:
                send_line(conn, "FEJL: KUNNE IKKE HENTE NYHEDER.")
                send(conn, "\r\n")
                continue

            # Artikelliste
            while True:
                pager = Pager(conn)
                pager.raw(divider())
                pager.line(f"{feed_name} - OVERSIGT:")
                pager.raw(divider())
                pager.line()

                for i, art in enumerate(articles[:15], 1):
                    # Nummer + Titel umgebrochen
                    prefix = f"{i:2}) "
                    first = True
                    for line in wrap(art["title"], WIDTH - len(prefix)):
                        if first:
                            pager.line(prefix + line)
                            first = False
                        else:
                            pager.line("    " + line)

                if pager.aborted:
                    break

                pager.raw(divider())
                pager.line("NUMMER = LYES ARTIKEL")
                pager.line("M       = TILBAGE TIL MENU")
                pager.line()

                send(conn, "DIT VALG: ")
                inp = recv_line(conn)

                if inp is None or inp == "M" or inp == "":
                    break
                if not inp.isdigit():
                    send_line(conn, "UGYLDIGT VALG.")
                    continue

                idx = int(inp) - 1
                if idx < 0 or idx >= min(15, len(articles)):
                    send_line(conn, "UGYLDIGT NUMMER.")
                    continue

                art = articles[idx]

                # Artikel-Vorschau
                pager = Pager(conn)
                pager.raw("\r\n")
                pager.raw(divider("="))
                pager.wrapped(art["title"])
                pager.raw(divider("="))
                pager.line()
                if art["teaser"]:
                    pager.wrapped(art["teaser"])
                    pager.line()

                if pager.aborted:
                    continue

                pager.raw(divider())
                pager.line("L = LAES HELE ARTIKLEN")
                pager.line("T = TILBAGE TIL OVERSIGT")
                pager.line()
                send(conn, "DIT VALG: ")

                c2 = recv_char(conn)
                send(conn, "\r\n")

                if c2 != "L":
                    continue

                # Volltext laden
                send(conn, "\r\n")
                send_line(conn, "HENTER ARTIKEL...")
                log(f"Henter: {art['link']}")

                paragraphs = fetch_full_article(art["link"])

                pager = Pager(conn)
                pager.raw("\r\n")
                pager.raw(divider("="))
                pager.wrapped(art["title"])
                pager.raw(divider("="))
                pager.line()

                for para in paragraphs:
                    if pager.aborted:
                        break
                    pager.wrapped(para)
                    pager.line()

                if not pager.aborted:
                    pager.raw(divider())
                    pager.line("TRYK EN TAST...")
                    recv_char(conn)

    except Exception as e:
        log(f"FEJL: {e}")
    finally:
        conn.close()
        log(f"FORBINDELSE LUKKET: {addr[0]}")


def start_server(port=PORT, log_callback=None):
    def log(msg):
        if log_callback:
            log_callback(msg)
        else:
            print(msg)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", port))
    server.listen(5)
    log(f"DR NYHEDER SERVER KLAR PAA PORT {port}")
    log(f"C64: ATDT<DIN-IP>:{port}")
    server.settimeout(1.0)
    return server


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    server = start_server(port)
    try:
        while True:
            try:
                conn, addr = server.accept()
                t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
                t.start()
            except socket.timeout:
                continue
    except KeyboardInterrupt:
        print("\nServer gestoppt.")
    finally:
        server.close()
