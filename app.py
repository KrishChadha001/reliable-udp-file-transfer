"""Small Tkinter presentation layer for the command-line transfer backend."""

import queue
import threading

try:
    import tkinter as tk
    from tkinter import filedialog, ttk
except ImportError as exc:
    raise SystemExit(
        "Tkinter is required for the GUI. Install it with: sudo apt install python3-tk"
    ) from exc

from rudp.models import Event, Settings
from rudp.receiver import receive_file
from rudp.sender import send_file


class TransferApp:
    """Keep widgets on the main thread while workers run UDP transfers."""

    def __init__(self, root):
        """Create two simple tabs and start the 100 ms event polling loop."""
        self.root = root
        self.root.title("Reliable UDP File Transfer")
        self.events = queue.Queue(maxsize=500)
        self.stop = threading.Event()
        self.worker = None
        self.cwnd_values = []
        self.send_counts = {"SEND": 0, "ACK": 0, "RESEND": 0, "TIMEOUT": 0}
        self.receive_counts = {"DUPLICATE": 0, "BUFFERED": 0, "DELIVERED": 0}
        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill="both", expand=True)
        self.send_tab = self._send_tab()
        self.receive_tab = self._receive_tab()
        self.root.after(100, self._poll)

    def _send_tab(self):
        """Build the sender controls without embedding networking code in widgets."""
        tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(tab, text="Send")
        self.send_file_var = tk.StringVar()
        self.send_host_var = tk.StringVar(value="127.0.0.1")
        self.send_port_var = tk.StringVar(value="9000")
        self.send_window_var = tk.StringVar(value="32")
        ttk.Button(tab, text="Choose file", command=self._choose_file).grid(row=0, column=0)
        ttk.Entry(tab, textvariable=self.send_file_var, width=50).grid(row=0, column=1)
        for row, label, variable in (
            (1, "Host", self.send_host_var),
            (2, "Port", self.send_port_var),
            (3, "Window", self.send_window_var),
        ):
            ttk.Label(tab, text=label).grid(row=row, column=0, sticky="w")
            ttk.Entry(tab, textvariable=variable).grid(row=row, column=1, sticky="ew")
        self.send_progress = ttk.Progressbar(tab, maximum=100)
        self.send_progress.grid(row=9, column=0, columnspan=2, sticky="ew")
        network = ttk.LabelFrame(tab, text="Simulated network")
        network.grid(row=4, column=0, columnspan=2, sticky="ew")
        self.send_network_vars = self._network_panel(network)
        self.send_counters = ttk.Label(tab, text="sent=0 acked=0 retransmissions=0 timeouts=0")
        self.send_counters.grid(row=10, column=0, columnspan=2, sticky="w")
        self.send_chart = tk.Canvas(tab, height=70, width=500, background="white")
        self.send_chart.grid(row=11, column=0, columnspan=2, sticky="ew")
        self.send_status = ttk.Label(tab, text="Ready")
        self.send_status.grid(row=12, column=0, columnspan=2, sticky="w")
        ttk.Button(tab, text="Start", command=self._start_send).grid(row=13, column=0)
        ttk.Button(tab, text="Cancel", command=self.stop.set).grid(row=13, column=1)
        self.send_log = tk.Text(tab, height=12, width=80, state="disabled")
        self.send_log.grid(row=14, column=0, columnspan=2)
        tab.columnconfigure(1, weight=1)
        return tab

    def _receive_tab(self):
        """Build receiver controls and keep save-path selection in the GUI layer."""
        tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(tab, text="Receive")
        self.receive_port_var = tk.StringVar(value="9000")
        self.receive_dir_var = tk.StringVar(value="received")
        ttk.Label(tab, text="Port").grid(row=0, column=0, sticky="w")
        ttk.Entry(tab, textvariable=self.receive_port_var).grid(row=0, column=1)
        ttk.Label(tab, text="Save folder").grid(row=1, column=0, sticky="w")
        ttk.Entry(tab, textvariable=self.receive_dir_var).grid(row=1, column=1, sticky="ew")
        network = ttk.LabelFrame(tab, text="Simulated network")
        network.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.receive_network_vars = self._network_panel(network)
        self.receive_progress = ttk.Progressbar(tab, maximum=100)
        self.receive_progress.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.receive_counters = ttk.Label(tab, text="duplicates=0 buffered=0 delivered=0")
        self.receive_counters.grid(row=4, column=0, columnspan=2, sticky="w")
        self.receive_status = ttk.Label(tab, text="Ready")
        self.receive_status.grid(row=5, column=0, columnspan=2, sticky="w")
        ttk.Button(tab, text="Listen", command=self._start_receive).grid(row=6, column=0)
        ttk.Button(tab, text="Stop", command=self.stop.set).grid(row=6, column=1)
        self.receive_log = tk.Text(tab, height=12, width=80, state="disabled")
        self.receive_log.grid(row=7, column=0, columnspan=2)
        tab.columnconfigure(1, weight=1)
        return tab

    @staticmethod
    def _network_panel(parent):
        """Create simple fault controls shared by both tabs."""
        variables = {}
        for row, (name, default) in enumerate(
            (("loss", "0"), ("dup", "0"), ("corrupt", "0"), ("delay", "0"), ("jitter", "0"))
        ):
            variables[name] = tk.StringVar(value=default)
            ttk.Label(parent, text=name + "%").grid(row=0, column=row * 2)
            ttk.Entry(parent, textvariable=variables[name], width=6).grid(row=0, column=row * 2 + 1)
        return variables

    def _choose_file(self):
        """Ask the user for a local file without touching the worker thread."""
        selected = filedialog.askopenfilename()
        if selected:
            self.send_file_var.set(selected)

    def _settings(self, variables=None):
        """Read the small set of GUI transport controls."""
        variables = variables or self.send_network_vars
        return Settings(
            window=int(self.send_window_var.get()),
            loss=float(variables["loss"].get()),
            dup=float(variables["dup"].get()),
            corrupt=float(variables["corrupt"].get()),
            delay_ms=float(variables["delay"].get()),
            jitter_ms=float(variables["jitter"].get()),
        )

    def _start_send(self):
        """Start a sender worker and leave all widget updates to the poller."""
        if self.worker and self.worker.is_alive():
            return
        self.stop.clear()
        path = self.send_file_var.get()
        self.worker = threading.Thread(
            target=self._send_worker,
            args=(path, self.send_host_var.get(), int(self.send_port_var.get())),
            daemon=True,
        )
        self.worker.start()

    def _send_worker(self, path, host, port):
        """Run sender networking away from Tk's event loop."""
        stats = send_file(path, host, port, self._settings(), self._queue_event, self.stop)
        self._queue_event(Event("RESULT", info=f"verified={stats.verified} {stats.error}"))

    def _start_receive(self):
        """Start a receiver worker and keep the main thread responsive."""
        if self.worker and self.worker.is_alive():
            return
        self.stop.clear()
        self.worker = threading.Thread(
            target=self._receive_worker,
            args=(int(self.receive_port_var.get()), self.receive_dir_var.get()),
            daemon=True,
        )
        self.worker.start()

    def _receive_worker(self, port, folder):
        """Run receiver networking away from Tk's event loop."""
        stats = receive_file(
            port,
            folder,
            self._settings(self.receive_network_vars),
            self._queue_event,
            self.stop,
        )
        self._queue_event(Event("RESULT", info=f"verified={stats.verified} {stats.error}"))

    def _queue_event(self, event: Event):
        """Bound the GUI queue so a fast transfer cannot exhaust memory."""
        try:
            self.events.put_nowait(event)
        except queue.Full:
            self.events.get_nowait()
            self.events.put_nowait(event)

    def _poll(self):
        """Drain worker events every 100 ms and update widgets only on the GUI thread."""
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            self._append(self.send_log, event)
            self._append(self.receive_log, event)
            if event.kind in self.send_counts:
                self.send_counts[event.kind] += 1
            if event.kind in self.receive_counts:
                self.receive_counts[event.kind] += 1
            self.send_counters.config(
                text=(
                    f"sent={self.send_counts['SEND']} acked={self.send_counts['ACK']} "
                    f"retransmissions={self.send_counts['RESEND']} "
                    f"timeouts={self.send_counts['TIMEOUT']}"
                )
            )
            self.receive_counters.config(
                text=(
                    f"duplicates={self.receive_counts['DUPLICATE']} "
                    f"buffered={self.receive_counts['BUFFERED']} "
                    f"delivered={self.receive_counts['DELIVERED']}"
                )
            )
            if event.kind in {"SEND", "ACK"}:
                self.cwnd_values.append(event.seq)
                self.cwnd_values = self.cwnd_values[-100:]
                self._draw_chart()
            if event.kind == "VERIFIED":
                self.send_status.config(text="SHA-256 verified")
                self.receive_status.config(text="SHA-256 verified")
            elif event.kind == "RESULT":
                self.send_status.config(text=event.info)
                self.receive_status.config(text=event.info)
        self.root.after(100, self._poll)

    def _draw_chart(self):
        """Draw a small event-sequence line without importing a plotting library."""
        self.send_chart.delete("all")
        if len(self.cwnd_values) < 2:
            return
        width = max(1, int(self.send_chart["width"]))
        height = max(1, int(self.send_chart["height"]))
        points = []
        for index, value in enumerate(self.cwnd_values):
            x = index * width / max(1, len(self.cwnd_values) - 1)
            y = height - (value % max(1, height - 5))
            points.extend((x, y))
        self.send_chart.create_line(*points, fill="blue")

    @staticmethod
    def _append(widget, event):
        """Append a bounded event-log line to a text widget."""
        widget.config(state="normal")
        widget.insert("end", f"{event.kind} {event.seq} {event.info}\n")
        lines = int(widget.index("end-1c").split(".")[0])
        if lines > 500:
            widget.delete("1.0", "2.0")
        widget.see("end")
        widget.config(state="disabled")


def main():
    """Launch the separate Tkinter presentation layer."""
    root = tk.Tk()
    TransferApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
