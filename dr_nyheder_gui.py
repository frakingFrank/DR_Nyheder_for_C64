#!/usr/bin/env python3
"""
DR Nyheder GUI Launcher
-----------------------
Startet den DR Nyheder C64 Server mit einer grafischen Oberfläche.
"""

import os
import sys
import json
import subprocess
import threading
import socket
import tkinter as tk
from tkinter import scrolledtext
import queue

CONFIG_FILE = os.path.expanduser("~/.dr_nyheder_config.json")
SERVER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dr_nyheder_server.py")

# C64-Farben (passend zur MOStodon GUI)
C64_BG     = "#0000a8"
C64_BORDER = "#3535c8"
C64_LBLUE  = "#7878f8"
C64_TEXT   = "#aaaaff"
C64_WHITE  = "#ffffff"
C64_YELLOW = "#f8f878"
C64_GREEN  = "#70a870"
C64_RED    = "#f87878"
C64_CYAN   = "#70d8d8"


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}


def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def get_local_ips():
    ips = []
    try:
        result = subprocess.run(["ip", "-4", "addr"], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("inet ") and "127.0.0.1" not in line \
               and "122." not in line and "172." not in line and "100." not in line:
                ip = line.split()[1].split("/")[0]
                hint = ""
                if "wlp" in line or "wlan" in line:
                    hint = "WLAN"
                elif "enx" in line or "eth" in line or "enp" in line:
                    hint = "LAN"
                ips.append((ip, hint))
    except Exception:
        pass
    return ips


def ensure_venv(venv_dir, log_fn):
    pip = os.path.join(venv_dir, "bin", "pip")
    python = os.path.join(venv_dir, "bin", "python")

    if not os.path.exists(python):
        log_fn("Erstelle venv...")
        subprocess.run([sys.executable, "-m", "venv", venv_dir], check=True)
        log_fn("venv erstellt.")

    log_fn("Installiere Pakete...")
    subprocess.run([pip, "install", "-q", "requests", "beautifulsoup4"], check=True)
    log_fn("Pakete OK.")
    return python


class DRNyhederGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("DR Nyheder - C64 Server")
        self.root.configure(bg=C64_BG)
        self.root.resizable(False, False)

        self.config = load_config()
        self.server_process = None
        self.running = False
        self.log_queue = queue.Queue()

        self._build_ui()
        self._refresh_ips()
        self._poll_log()

    def _build_ui(self):
        outer = tk.Frame(self.root, bg=C64_BORDER, padx=8, pady=8)
        outer.pack()
        inner = tk.Frame(outer, bg=C64_BG, padx=16, pady=12)
        inner.pack()

        # Titel
        tk.Label(inner, text="* DR NYHEDER - C64 SERVER *",
                 font=("Courier", 16, "bold"),
                 fg=C64_CYAN, bg=C64_BG).grid(row=0, column=0, columnspan=3, pady=(0, 2))
        tk.Label(inner, text="nyheder fra dr.dk til din C64",
                 font=("Courier", 9), fg=C64_LBLUE, bg=C64_BG).grid(
                 row=1, column=0, columnspan=3, pady=(0, 12))

        # --- Port ---
        self._section(inner, "INDSTILLINGER", row=2)

        tk.Label(inner, text="Port:", font=("Courier", 10),
                 fg=C64_TEXT, bg=C64_BG, anchor="w").grid(row=3, column=0, sticky="w", pady=3)
        self.port_var = tk.StringVar(value=str(self.config.get("port", 6510)))
        tk.Entry(inner, textvariable=self.port_var, width=10,
                 font=("Courier", 10), bg=C64_BORDER, fg=C64_WHITE,
                 insertbackground=C64_WHITE, relief="flat", bd=4).grid(
                 row=3, column=1, sticky="w", padx=6, pady=3)

        tk.Label(inner, text="(standard: 6510)", font=("Courier", 9),
                 fg=C64_LBLUE, bg=C64_BG).grid(row=3, column=2, sticky="w")

        # --- Netzwerk ---
        self._section(inner, "NETZWERK", row=4)

        self.ip_label = tk.Label(inner, text="", font=("Courier", 10),
                                  fg=C64_YELLOW, bg=C64_BG, justify="left", anchor="w")
        self.ip_label.grid(row=5, column=0, columnspan=3, sticky="w", pady=2)

        tk.Button(inner, text="IP aktualisieren", font=("Courier", 9),
                  bg=C64_BORDER, fg=C64_TEXT, activebackground=C64_LBLUE,
                  relief="flat", bd=0, padx=8, pady=2,
                  command=self._refresh_ips).grid(row=6, column=0, columnspan=3,
                  sticky="w", pady=(0, 8))

        # --- Feeds ---
        self._section(inner, "VERFUEGBARE FEEDS", row=7)

        feeds_text = (
            "1) Seneste nyt    6) Viden\n"
            "2) Indland        7) Kultur\n"
            "3) Udland         8) Sporten\n"
            "4) Politik        9) Syd/Soenderjylland\n"
            "5) Penge"
        )
        tk.Label(inner, text=feeds_text, font=("Courier", 9),
                 fg=C64_TEXT, bg=C64_BG, justify="left", anchor="w").grid(
                 row=8, column=0, columnspan=3, sticky="w", pady=4)

        # --- Buttons ---
        btn_frame = tk.Frame(inner, bg=C64_BG)
        btn_frame.grid(row=9, column=0, columnspan=3, pady=10)

        self.start_btn = tk.Button(btn_frame, text="▶  SERVER STARTEN",
                                    font=("Courier", 12, "bold"),
                                    bg=C64_GREEN, fg=C64_BG,
                                    activebackground=C64_WHITE,
                                    relief="flat", bd=0, padx=16, pady=8,
                                    command=self._start)
        self.start_btn.pack(side="left", padx=6)

        self.stop_btn = tk.Button(btn_frame, text="■  STOPPEN",
                                   font=("Courier", 12, "bold"),
                                   bg=C64_RED, fg=C64_BG,
                                   activebackground=C64_WHITE,
                                   relief="flat", bd=0, padx=16, pady=8,
                                   state="disabled",
                                   command=self._stop)
        self.stop_btn.pack(side="left", padx=6)

        # --- Log ---
        self._section(inner, "SERVER-LOG", row=10)

        self.log = scrolledtext.ScrolledText(inner, width=58, height=12,
                                              font=("Courier", 9),
                                              bg="#000030", fg=C64_TEXT,
                                              insertbackground=C64_WHITE,
                                              relief="flat", bd=4,
                                              state="disabled")
        self.log.grid(row=11, column=0, columnspan=3, pady=4)

        self.status_var = tk.StringVar(value="KLAR.")
        tk.Label(inner, textvariable=self.status_var,
                 font=("Courier", 10, "bold"),
                 fg=C64_YELLOW, bg=C64_BG).grid(row=12, column=0, columnspan=3, pady=(4, 0))

    def _section(self, parent, title, row):
        tk.Label(parent, text=f" {title} ",
                 font=("Courier", 9, "bold"),
                 fg=C64_BG, bg=C64_LBLUE).grid(row=row, column=0, columnspan=3,
                                                sticky="w", pady=(10, 2))

    def _refresh_ips(self):
        port = self.port_var.get().strip() or "6510"
        ips = get_local_ips()
        if ips:
            lines = [f"atdt{ip}:{port}  [{hint}]" for ip, hint in ips]
            self.ip_label.config(text="\n".join(lines))
        else:
            self.ip_label.config(text="(Keine IP gefunden)")

    def _log(self, text, kind="normal"):
        self.log_queue.put((kind, text))

    def _poll_log(self):
        colors = {"normal": C64_TEXT, "yellow": C64_YELLOW,
                  "green": C64_GREEN, "red": C64_RED, "cyan": C64_CYAN}
        try:
            while True:
                kind, text = self.log_queue.get_nowait()
                self.log.config(state="normal")
                tag = f"c_{kind}"
                self.log.tag_config(tag, foreground=colors.get(kind, C64_TEXT))
                self.log.insert("end", text + "\n", tag)
                self.log.see("end")
                self.log.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log)

    def _start(self):
        port = self.port_var.get().strip()
        if not port.isdigit():
            self._log("FEHLER: Ungültiger Port!", "red")
            return

        self.config["port"] = int(port)
        save_config(self.config)

        self._log("=" * 40, "cyan")
        self._refresh_ips()

        def run():
            try:
                # venv im selben Ordner wie das Script
                script_dir = os.path.dirname(os.path.abspath(__file__))
                venv_dir = os.path.join(script_dir, "dr_venv")
                python = ensure_venv(venv_dir, lambda m: self._log(m))

                self._log(f"Server startet auf Port {port}...", "green")
                self.running = True
                self.root.after(0, self._update_buttons)

                self.server_process = subprocess.Popen(
                    [python, SERVER_SCRIPT, port],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True, bufsize=1
                )

                for line in self.server_process.stdout:
                    self._log(line.rstrip())

                self.server_process.wait()
                self._log("Server beendet.", "yellow")
                self.running = False
                self.root.after(0, self._update_buttons)

            except Exception as e:
                self._log(f"FEHLER: {e}", "red")
                self.running = False
                self.root.after(0, self._update_buttons)

        threading.Thread(target=run, daemon=True).start()

    def _stop(self):
        if self.server_process:
            self.server_process.terminate()
        self.running = False
        self._log("Server gestoppt.", "yellow")
        self._update_buttons()

    def _update_buttons(self):
        if self.running:
            self.start_btn.config(state="disabled")
            self.stop_btn.config(state="normal")
            self.status_var.set("SERVER LAEUFT...")
        else:
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
            self.status_var.set("KLAR.")


def main():
    try:
        root = tk.Tk()
    except Exception:
        print("FEHLER: tkinter nicht verfuegbar.")
        print("Installieren: sudo apt install python3-tk")
        sys.exit(1)

    app = DRNyhederGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
