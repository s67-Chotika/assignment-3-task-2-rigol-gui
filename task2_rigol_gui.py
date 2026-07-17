"""Compact Tkinter companion for RIGOL MSO/DS1000Z oscilloscopes."""

import csv
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
import pyvisa
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.ticker import MultipleLocator


class ScopeController:
    """Keep all VISA and SCPI communication separate from the GUI."""

    CHANNELS = {f"CH{number}": f"CHANnel{number}" for number in range(1, 5)}

    def __init__(self, backend="@py", timeout=5000):
        """Initialize VISA configuration and the disconnected instrument state."""
        self.backend = backend
        self.timeout = timeout
        self.resource_manager = None
        self.scope = None
        self.resource_name = None
        self.instrument_id = None

    # VISA discovery and connection management
    def _create_resource_manager(self):
        """Create the VISA resource manager only when it is first needed."""
        if self.resource_manager is None:
            self.resource_manager = pyvisa.ResourceManager(self.backend)

    def search(self):
        """Return the USB VISA resource names detected by PyVISA."""
        self._create_resource_manager()
        return [
            resource
            for resource in self.resource_manager.list_resources()
            if resource.upper().startswith("USB")
        ]

    def connect(self, resource_name):
        """Open the selected VISA resource and return its identity string."""
        if not resource_name:
            raise RuntimeError("Please select a VISA device first.")
        if self.scope is not None:
            self.disconnect()

        self._create_resource_manager()
        try:
            self.scope = self.resource_manager.open_resource(resource_name)
            self.scope.timeout = self.timeout
            # A USB-sized chunk prevents pyvisa-py from waiting for a 1 MB read.
            self.scope.chunk_size = 1024
            self.scope.write_termination = "\n"
            self.scope.read_termination = "\n"
            self.resource_name = resource_name
            self.instrument_id = self.query("*IDN?")
            return self.instrument_id
        except Exception:
            self.disconnect()
            raise

    def disconnect(self):
        """Close the instrument and resource manager safely."""
        if self.scope is not None:
            try:
                self.scope.close()
            finally:
                self.scope = None
        if self.resource_manager is not None:
            try:
                self.resource_manager.close()
            finally:
                self.resource_manager = None
        self.resource_name = None
        self.instrument_id = None

    def ensure_connected(self):
        """Raise an error when an operation is requested while disconnected."""
        if self.scope is None:
            raise RuntimeError("The oscilloscope is not connected.")

    # Generic SCPI input and output helpers
    def write(self, command):
        """Send a write-only SCPI command to the oscilloscope."""
        self.ensure_connected()
        self.scope.write(command)

    def query(self, command):
        """Send a SCPI query and return the stripped instrument response."""
        self.ensure_connected()
        return self.scope.query(command).strip()

    def query_float(self, command):
        """Send a SCPI query and convert its response to a float."""
        return float(self.query(command))

    @classmethod
    def source(cls, channel):
        """Convert a GUI channel label into its RIGOL SCPI source name."""
        try:
            return cls.CHANNELS[channel]
        except KeyError as error:
            raise ValueError(f"Invalid channel: {channel}") from error

    # Acquisition, channel, and timebase controls
    def run(self):  # Start continuous waveform acquisition.
        self.write(":RUN")
    def stop(self):  # Stop waveform acquisition.
        self.write(":STOP")
    def single(self):  # Arm one triggered acquisition.
        self.write(":SINGle")

    def auto_set(self):
        """Run the RIGOL autoscale sequence used by MSO/DS1000Z models."""
        # MSO/DS1000Z uses AUToscale, not AUToset.
        self.write(":SYSTem:AUToscale ON")
        self.write(":AUToscale")

    def trigger_status(self):
        """Return the current oscilloscope trigger state."""
        return self.query(":TRIGger:STATus?").upper()

    def set_channel_display(self, channel, displayed):
        """Enable or disable the selected channel display."""
        state = "ON" if displayed else "OFF"
        self.write(f":{self.source(channel)}:DISPlay {state}")

    def set_channel_scale(self, channel, volts_per_div):
        """Set the vertical volts-per-division value for one channel."""
        self.write(f":{self.source(channel)}:SCALe {volts_per_div}")

    def get_channel_display(self, channel):  # Read a channel's display state.
        return bool(int(float(self.query(f":{self.source(channel)}:DISPlay?"))))

    def get_channel_scale(self, channel):  # Read a channel's V/div setting.
        return self.query_float(f":{self.source(channel)}:SCALe?")

    def set_time_scale(self, seconds_per_div):
        """Set the horizontal seconds-per-division value."""
        self.write(f":TIMebase:MAIN:SCALe {seconds_per_div}")

    def get_time_scale(self):  # Read the horizontal T/div setting.
        return self.query_float(":TIMebase:MAIN:SCALe?")

    # Binary waveform and screenshot transfers
    def _query_binary_block(self, command, timeout=30000, minimum_size=0):
        """Read a definite-length IEEE block reliably through pyvisa-py."""
        self.ensure_connected()
        old_timeout = self.scope.timeout
        old_read_termination = self.scope.read_termination
        try:
            self.scope.timeout = timeout
            self.scope.read_termination = None
            self.scope.write(command)

            prefix = bytes(self.scope.read_bytes(2, break_on_termchar=False))
            if len(prefix) != 2 or prefix[:1] != b"#":
                raise RuntimeError(f"Invalid binary block header: {prefix!r}")

            try:
                digit_count = int(prefix[1:2].decode("ascii"))
            except (ValueError, UnicodeDecodeError) as error:
                raise RuntimeError(f"Invalid binary length header: {prefix!r}") from error
            if digit_count <= 0:
                raise RuntimeError("Indefinite binary blocks are not supported.")

            length_text = bytes(
                self.scope.read_bytes(digit_count, break_on_termchar=False)
            )
            try:
                payload_length = int(length_text.decode("ascii"))
            except (ValueError, UnicodeDecodeError) as error:
                raise RuntimeError(f"Invalid payload length: {length_text!r}") from error

            payload = bytes(
                self.scope.read_bytes(
                    payload_length,
                    chunk_size=1024,
                    break_on_termchar=False,
                )
            )

            # RIGOL may append CR/LF outside the advertised block length.
            self.scope.timeout = 100
            for _ in range(2):
                try:
                    trailing = bytes(
                        self.scope.read_bytes(1, break_on_termchar=False)
                    )
                except Exception:
                    break
                if trailing not in (b"\r", b"\n"):
                    raise RuntimeError(
                        f"Unexpected byte after {command}: {trailing!r}"
                    )

            if len(payload) != payload_length:
                raise RuntimeError(
                    f"Expected {payload_length} bytes, received {len(payload)}."
                )
            if minimum_size and payload_length < minimum_size:
                raise RuntimeError(
                    f"Expected at least {minimum_size} bytes, received {payload_length}."
                )
            return payload
        finally:
            self.scope.timeout = old_timeout
            self.scope.read_termination = old_read_termination

    def acquire_waveform(self, channel):
        """Read and convert waveform samples for one enabled channel."""
        source = self.source(channel)
        self.write(f":WAVeform:SOURce {source}")
        self.write(":WAVeform:MODE NORMal")
        self.write(":WAVeform:FORMat BYTE")

        preamble_text = self.query(":WAVeform:PREamble?")
        preamble = [float(item) for item in preamble_text.split(",")]
        if len(preamble) < 10:
            raise RuntimeError(f"Invalid waveform preamble for {channel}.")

        returned_points = int(preamble[2])
        x_increment, x_origin, x_reference = preamble[4:7]
        y_increment, y_origin, y_reference = preamble[7:10]
        payload = self._query_binary_block(
            ":WAVeform:DATA?", timeout=30000, minimum_size=1
        )
        raw = np.frombuffer(payload, dtype=np.uint8)
        point_count = min(raw.size, returned_points)
        if point_count < 2:
            raise RuntimeError(f"Not enough waveform points returned from {channel}.")
        raw = raw[:point_count].astype(float)
        indexes = np.arange(point_count, dtype=float)
        time_values = (indexes - x_reference) * x_increment + x_origin
        voltage_values = (raw - y_origin - y_reference) * y_increment
        # BYTE waveform values use code 127 as the vertical screen centre.
        # Keep this display coordinate separate from the calibrated voltage
        # so moving a channel with the scope's Position knob is reproduced.
        display_values = (raw - 127.0) * y_increment
        return {
            "channel": channel,
            "time": time_values,
            "voltage": voltage_values,
            "display_voltage": display_values,
            "points": point_count,
        }

    def capture_screenshot(self):
        """Request and validate a PNG screenshot from the oscilloscope."""
        command = ":DISPlay:DATA? ON,OFF,PNG"
        data = self._query_binary_block(command, timeout=30000)
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("The oscilloscope did not return a valid PNG image.")
        return data


class RigolApp:
    """Compact capture-and-export UI rather than a duplicate front panel."""

    CHANNEL_COLORS = {
        "CH1": "#f2c94c",
        "CH2": "#22d3d6",
        "CH3": "#ed5bb5",
        "CH4": "#3b9cff",
    }
    VOLTAGE_SCALES = {
        **{f"{n} mV/div": n / 1000 for n in (1, 2, 5, 10, 20, 50, 100, 200, 500)},
        **{f"{n} V/div": float(n) for n in (1, 2, 5, 10)},
    }
    TIME_SCALES = {
        f"{n} {unit}/div": n * factor
        for unit, factor, values in (
            ("ns", 1e-9, (5, 10, 20, 50, 100, 200, 500)),
            ("us", 1e-6, (1, 2, 5, 10, 20, 50, 100, 200, 500)),
            ("ms", 1e-3, (1, 2, 5, 10, 20, 50, 100, 200, 500)),
            ("s", 1.0, (1, 2, 5, 10, 20, 50)),
        )
        for n in values
    }

    def __init__(self, root, controller=None):
        """Initialize application state, styles, variables, and widgets."""
        self.root = root
        self.controller = controller or ScopeController()
        self.waveform_data = {}
        self.single_poll_count = 0
        self.refresh_job = None
        self.connected_widgets = []
        self.style = ttk.Style(root)
        for name, color in (("Run", "#159447"), ("Stop", "#c73535")):
            self.style.configure(f"{name}.TButton", foreground=color)
            self.style.map(
                f"{name}.TButton",
                foreground=[("disabled", "#888888"), ("!disabled", color)],
            )
        self.style.configure("Connected.TLabel", foreground="#159447")
        self.style.configure("Disconnected.TLabel", foreground="#c73535")
        for channel, color in self.CHANNEL_COLORS.items():
            style_name = f"{channel}.TCheckbutton"
            self.style.configure(style_name, foreground=color)
            self.style.map(
                style_name,
                foreground=[("disabled", "#888888"), ("!disabled", color)],
            )

        self.device_var = tk.StringVar()
        self.status_var = tk.StringVar(value="DISCONNECTED")
        self.command_var = tk.StringVar(value="*IDN?")
        self.time_scale_var = tk.StringVar(value="1 ms/div")
        self.channel_vars = {
            channel: {
                "display": tk.BooleanVar(value=(channel == "CH1")),
                "scale": tk.StringVar(value="1 V/div"),
            }
            for channel in self.CHANNEL_COLORS
        }

        self._configure_window()
        self._create_widgets()
        self._style_graph("Connect a scope, then select Update Graph")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.search_devices(show_error=False)

    # Window layout and widget construction
    def _configure_window(self):
        """Set the window title, size limits, and responsive grid weights."""
        self.root.title("RIGOL Oscilloscope Capture Companion")
        self.root.geometry("1200x760")
        self.root.minsize(900, 620)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

    def _create_widgets(self):
        """Build and arrange all major sections of the application window."""
        self._create_instrument_section()
        self._create_command_section()

        body = ttk.Frame(self.root)
        body.grid(row=2, column=0, sticky="nsew", padx=8, pady=4)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        controls = ttk.Frame(body, width=230)
        controls.grid(row=0, column=0, sticky="nsw", padx=(0, 8))
        controls.grid_propagate(False)
        self._create_scope_controls(controls)
        self._create_channel_controls(controls)
        self._create_horizontal_controls(controls)
        self._create_export_controls(controls)
        self._create_waveform_section(body)
        self._create_log_section()
        self._create_status_bar()

    def _create_instrument_section(self):
        """Create device selection, connection buttons, and status display."""
        frame = ttk.LabelFrame(self.root, text="1. Instrument")
        frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="Device:").grid(row=0, column=0, padx=5, pady=5)
        self.device_box = ttk.Combobox(
            frame, textvariable=self.device_var, state="readonly"
        )
        self.device_box.grid(row=0, column=1, sticky="ew", padx=5, pady=5)
        buttons = (
            ("Search", "search_button", self.search_devices, "normal"),
            ("Connect", "connect_button", self.connect_scope, "disabled"),
            ("Disconnect", "disconnect_button", self.disconnect_scope, "disabled"),
        )
        for column, (text, attribute, command, state) in enumerate(buttons, start=2):
            button = ttk.Button(frame, text=text, command=command, state=state)
            button.grid(row=0, column=column, padx=3)
            setattr(self, attribute, button)
        ttk.Label(frame, text="Status:").grid(row=0, column=5, padx=(12, 2))
        self.status_label = ttk.Label(
            frame,
            textvariable=self.status_var,
            style="Disconnected.TLabel",
        )
        self.status_label.grid(row=0, column=6, padx=5)

    def _create_command_section(self):
        """Create the SCPI command entry and scrollable response display."""
        frame = ttk.LabelFrame(self.root, text="2. SCPI Command")
        frame.grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="Command:").grid(row=0, column=0, padx=5, pady=5)
        entry = ttk.Entry(frame, textvariable=self.command_var)
        entry.grid(row=0, column=1, sticky="ew", padx=5, pady=5)
        entry.bind("<Return>", lambda _event: self.send_command())
        self.send_button = ttk.Button(
            frame,
            text="Send",
            command=self.send_command,
            state="disabled",
        )
        self.send_button.grid(row=0, column=2, padx=3)
        self.connected_widgets.append((self.send_button, "normal"))
        ttk.Button(frame, text="Clear Response", command=self.clear_response).grid(
            row=0, column=3, padx=5
        )
        ttk.Label(frame, text="Response:").grid(
            row=1, column=0, sticky="nw", padx=5, pady=(0, 5)
        )
        self.response_text = tk.Text(frame, height=3, wrap="word")
        response_scrollbar = ttk.Scrollbar(frame, command=self.response_text.yview)
        self.response_text.configure(yscrollcommand=response_scrollbar.set)
        self.response_text.grid(
            row=1,
            column=1,
            columnspan=2,
            sticky="ew",
            padx=(5, 0),
            pady=(0, 5),
        )
        response_scrollbar.grid(
            row=1,
            column=3,
            sticky="ns",
            padx=(0, 5),
            pady=(0, 5),
        )

    def _create_scope_controls(self, parent):
        """Create Run, Stop, Single, and Auto Set controls."""
        frame = ttk.LabelFrame(parent, text="3. Scope Controls")
        frame.pack(fill="x", pady=(0, 3))
        buttons = (
            ("Run", self.run_scope, "Run.TButton"),
            ("Stop", self.stop_scope, "Stop.TButton"),
            ("Single", self.single_scope, None),
            ("Auto Set", self.auto_set, None),
        )
        for index, (text, command, style_name) in enumerate(buttons):
            row, column = divmod(index, 2)
            button = ttk.Button(
                frame,
                text=text,
                command=command,
                state="disabled",
                style=style_name or "TButton",
            )
            button.grid(
                row=row,
                column=column,
                sticky="ew",
                padx=4,
                pady=2,
            )
            if text == "Run":
                self.run_button = button
            elif text == "Stop":
                self.stop_button = button
            self.connected_widgets.append((button, "normal"))
        frame.columnconfigure((0, 1), weight=1)

    def _create_channel_controls(self, parent):
        """Create display and V/div controls for channels CH1 through CH4."""
        frame = ttk.LabelFrame(parent, text="4. Channels")
        frame.pack(fill="x", pady=3)
        ttk.Label(frame, text="Display").grid(row=0, column=0, padx=4)
        ttk.Label(frame, text="V/div").grid(row=0, column=1, padx=4)
        for row, channel in enumerate(self.CHANNEL_COLORS, start=1):
            check = ttk.Checkbutton(
                frame,
                text=channel,
                style=f"{channel}.TCheckbutton",
                variable=self.channel_vars[channel]["display"],
                command=lambda ch=channel: self.apply_channel(ch),
            )
            check.grid(row=row, column=0, sticky="w", padx=4, pady=2)
            check.configure(state="disabled")
            self.connected_widgets.append((check, "normal"))
            combo = ttk.Combobox(
                frame,
                textvariable=self.channel_vars[channel]["scale"],
                values=list(self.VOLTAGE_SCALES),
                state="readonly",
                width=12,
            )
            combo.grid(row=row, column=1, padx=4, pady=2)
            combo.bind(
                "<<ComboboxSelected>>",
                lambda _event, ch=channel: self.apply_channel(ch),
            )
            combo.configure(state="disabled")
            self.connected_widgets.append((combo, "readonly"))

    def _create_horizontal_controls(self, parent):
        """Create the horizontal time-per-division control."""
        frame = ttk.LabelFrame(parent, text="5. Horizontal")
        frame.pack(fill="x", pady=3)
        ttk.Label(frame, text="T/div:").pack(anchor="w", padx=5, pady=(2, 0))
        combo = ttk.Combobox(
            frame,
            textvariable=self.time_scale_var,
            values=list(self.TIME_SCALES),
            state="readonly",
        )
        combo.pack(fill="x", padx=5, pady=3)
        combo.bind("<<ComboboxSelected>>", lambda _event: self.apply_time_scale())
        combo.configure(state="disabled")
        self.connected_widgets.append((combo, "readonly"))

    def _create_export_controls(self, parent):
        """Create controls for graph, scope-screen, and CSV exports."""
        frame = ttk.LabelFrame(parent, text="7. Export")
        frame.pack(fill="x", pady=3)
        items = (
            ("Save Graph", "save_graph_button", self.save_graph, False),
            ("Save Scope PNG", "save_scope_button", self.save_scope_png, True),
            ("Save Waveform CSV", "save_csv_button", self.save_csv, False),
        )
        for text, attribute, command, enable_on_connect in items:
            button = ttk.Button(frame, text=text, command=command, state="disabled")
            button.pack(fill="x", padx=5, pady=2)
            setattr(self, attribute, button)
            if enable_on_connect:
                self.connected_widgets.append((button, "normal"))

    def _create_waveform_section(self, parent):
        """Create the Matplotlib waveform canvas and update button."""
        frame = ttk.LabelFrame(parent, text="6. Waveform")
        frame.grid(row=0, column=1, sticky="nsew")
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill="x", padx=5, pady=(3, 0))
        self.update_graph_button = ttk.Button(
            toolbar,
            text="Update Graph",
            command=self.refresh_waveforms,
            state="disabled",
        )
        self.update_graph_button.pack(side="right")
        self.connected_widgets.append((self.update_graph_button, "normal"))
        self.figure = Figure(figsize=(8, 5), dpi=100)
        self.axes = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    def _create_log_section(self):
        """Create the scrollable activity and error log."""
        frame = ttk.LabelFrame(self.root, text="8. Activity / Error Log")
        frame.grid(row=3, column=0, sticky="ew", padx=8, pady=4)
        self.log_text = tk.Text(frame, height=4, wrap="word")
        scrollbar = ttk.Scrollbar(frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)
        scrollbar.pack(side="right", fill="y", padx=(0, 4), pady=4)

    def _create_status_bar(self):
        """Create the status bar that displays the connected instrument ID."""
        self.instrument_label = ttk.Label(
            self.root,
            text="Instrument: Not connected",
        )
        self.instrument_label.grid(row=4, column=0, sticky="w", padx=9, pady=(0, 5))

    def _style_graph(self, title=""):
        """Clear and style the graph as a 12-by-8 oscilloscope grid."""
        self.axes.clear()
        self.axes.set_facecolor("#101316")
        self.figure.patch.set_facecolor("#101316")
        # Keep the 12 x 8 scope grid, with a small margin so traces remain visible.
        self.axes.set_xlim(-6.15, 6.15)
        self.axes.set_ylim(-4.2, 4.2)
        self.axes.xaxis.set_major_locator(MultipleLocator(1))
        self.axes.yaxis.set_major_locator(MultipleLocator(1))
        self.axes.grid(True, color="#4d565f", alpha=0.7)
        self.axes.axvline(0, color="#d27a22", linestyle="--", linewidth=0.8)
        self.axes.tick_params(colors="#d7dde3")
        for spine in self.axes.spines.values():
            spine.set_color("#89929b")
        self.axes.set_title(title, color="#d7dde3", fontsize=11)
        self.axes.set_xlabel("Horizontal divisions", color="#d7dde3")
        self.axes.set_ylabel("Vertical divisions", color="#d7dde3")
        if hasattr(self, "canvas"):
            self.canvas.draw_idle()

    @staticmethod
    def closest_label(options, value):
        """Return the UI scale label whose numeric value is the closest match."""
        return min(options, key=lambda label: abs(options[label] - value))

    # Logging, errors, and GUI state helpers
    def log(self, message):
        """Append a timestamped activity message to the system log."""
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{stamp}] {message}\n")
        self.log_text.see("end")

    def show_error(self, title, error, popup=True):
        """Record an error and optionally show it in a message box."""
        self.log(f"{title}: {error}")
        if popup:
            messagebox.showerror(title, str(error))

    def clear_response(self):  # Clear displayed responses without undoing commands.
        self.response_text.delete("1.0", "end")

    def selected_channels(self):  # Return the channels enabled in the GUI.
        return [
            channel
            for channel, settings in self.channel_vars.items()
            if settings["display"].get()
        ]

    def update_connection_controls(self, connected):
        """Enable instrument controls only while a scope is connected."""
        for widget, enabled_state in self.connected_widgets:
            widget.configure(state=enabled_state if connected else "disabled")

        self.status_var.set("CONNECTED" if connected else "DISCONNECTED")
        self.status_label.configure(
            style="Connected.TLabel" if connected else "Disconnected.TLabel"
        )
        self.search_button.configure(state="disabled" if connected else "normal")
        self.disconnect_button.configure(state="normal" if connected else "disabled")
        self.device_box.configure(state="disabled" if connected else "readonly")
        self.connect_button.configure(
            state=(
                "disabled"
                if connected or not self.device_var.get()
                else "normal"
            )
        )

    def update_run_stop_buttons(self, running):
        """Disable the command that already matches the scope state."""
        if self.controller.scope is None:
            self.run_button.configure(state="disabled")
            self.stop_button.configure(state="disabled")
        elif running:
            self.run_button.configure(state="disabled")
            self.stop_button.configure(state="normal")
        else:
            self.run_button.configure(state="normal")
            self.stop_button.configure(state="disabled")

    def sync_run_stop_buttons(self):
        """Query trigger status and synchronize the Run/Stop button states."""
        status = self.controller.trigger_status()
        self.update_run_stop_buttons(status != "STOP")

    # Connection and instrument event callbacks
    def search_devices(self, show_error=True):
        """Scan for USB VISA instruments and populate the device list."""
        try:
            resources = self.controller.search()
            self.device_box["values"] = resources
            if resources:
                self.device_var.set(resources[0])
                self.connect_button.configure(state="normal")
                self.log(f"Found {len(resources)} VISA device(s).")
            else:
                self.device_var.set("")
                self.connect_button.configure(state="disabled")
                self.log("No VISA devices found. Connect the scope, then select Search.")
        except Exception as error:
            self.device_box["values"] = ()
            self.device_var.set("")
            self.connect_button.configure(state="disabled")
            self.show_error("Search Error", error, popup=show_error)

    def connect_scope(self):
        """Connect to the selected device and synchronize its current settings."""
        try:
            identity = self.controller.connect(self.device_var.get())
            self.instrument_label.configure(text=f"Instrument: {identity}")
            self.update_connection_controls(True)
            self.log(f"Connected: {identity}")
            self.sync_settings()
            self.sync_run_stop_buttons()
            messagebox.showinfo(
                "Connection Successful",
                f"Connected to:\n{self.device_var.get()}\n\n{identity}",
            )
        except Exception as error:
            self.controller.disconnect()
            self.instrument_label.configure(text="Instrument: Not connected")
            self.update_connection_controls(False)
            self.show_error("Connection Error", error)

    def disconnect_scope(self):
        """Disconnect cleanly and return all controls to their initial state."""
        was_connected = self.controller.scope is not None
        self.controller.disconnect()
        self.instrument_label.configure(text="Instrument: Not connected")
        self.update_connection_controls(False)
        self.log("Disconnected.")
        if was_connected:
            messagebox.showinfo(
                "Disconnected",
                "The oscilloscope has been disconnected.",
            )

    def send_command(self):
        """Execute the manually entered SCPI command or query."""
        command = self.command_var.get().strip()
        if not command:
            messagebox.showwarning("Empty Command", "Please enter a SCPI command.")
            return
        try:
            self.response_text.insert("end", f"> {command}\n")
            if "?" in command:
                response = self.controller.query(command)
                self.response_text.insert(
                    "end", f"< {response or '[empty response]'}\n"
                )
                self.log("SCPI query completed.")
            else:
                self.controller.write(command)
                self.response_text.insert(
                    "end", "Command sent - no response expected.\n"
                )
                action = command.upper().lstrip(":").split()[0]
                if action == "RUN":
                    self.update_run_stop_buttons(True)
                elif action == "STOP":
                    self.update_run_stop_buttons(False)
                elif action.startswith("SING"):
                    self.update_run_stop_buttons(True)
                self.log("SCPI command completed.")
            self.response_text.see("end-1c")
        except Exception as error:
            self.show_error("SCPI Error", error)

    def run_scope(self):
        """Handle the Run button and update the acquisition controls."""
        try:
            self.controller.run()
            self.update_run_stop_buttons(True)
            self.log("Scope running.")
        except Exception as error:
            self.show_error("Run Error", error)

    def stop_scope(self):
        """Handle the Stop button and update the acquisition controls."""
        try:
            self.controller.stop()
            self.update_run_stop_buttons(False)
            self.log("Scope stopped.")
        except Exception as error:
            self.show_error("Stop Error", error)

    def single_scope(self):
        """Arm a single acquisition and begin polling for completion."""
        try:
            self.controller.single()
            self.update_run_stop_buttons(True)
            self.single_poll_count = 0
            self.log("Single acquisition armed; waiting for a trigger...")
            self.root.after(250, self._wait_for_single)
        except Exception as error:
            self.show_error("Single Error", error)

    def _wait_for_single(self):
        """Poll trigger status until Single completes or the wait limit is reached."""
        if self.controller.scope is None:
            return
        try:
            status = self.controller.trigger_status()
            self.single_poll_count += 1
            if status == "STOP":
                self.update_run_stop_buttons(False)
                self.log("Single acquisition completed; scope stopped.")
                self.root.after(300, self.refresh_waveforms)
            elif self.single_poll_count < 40:
                self.root.after(250, self._wait_for_single)
            else:
                self.log("Still waiting for a trigger; the existing graph was kept.")
        except Exception as error:
            self.show_error("Single Status Error", error, popup=False)

    def auto_set(self):
        """Start Auto Set and schedule synchronization after autoscaling."""
        try:
            self.controller.auto_set()
            self.update_run_stop_buttons(True)
            self.log("Auto Set started...")
            self.root.after(2500, self._finish_auto_set)
        except Exception as error:
            self.show_error("Auto Set Error", error)

    def _finish_auto_set(self):
        """Synchronize settings and refresh the graph after Auto Set finishes."""
        if self.controller.scope is None:
            return
        try:
            self.sync_settings()
            self.sync_run_stop_buttons()
            self.log("Auto Set completed; settings synchronized.")
            self.refresh_waveforms()
        except Exception as error:
            self.show_error("Auto Set Sync Error", error)

    def sync_settings(self):
        """Read channel and timebase settings from the physical oscilloscope."""
        for channel, settings in self.channel_vars.items():
            displayed = self.controller.get_channel_display(channel)
            scale = self.controller.get_channel_scale(channel)
            settings["display"].set(displayed)
            settings["scale"].set(self.closest_label(self.VOLTAGE_SCALES, scale))
        time_scale = self.controller.get_time_scale()
        self.time_scale_var.set(self.closest_label(self.TIME_SCALES, time_scale))

    def apply_channel(self, channel):
        """Apply one channel's display and V/div settings to the oscilloscope."""
        settings = self.channel_vars[channel]
        displayed = settings["display"].get()
        try:
            self.controller.set_channel_display(channel, displayed)
            if displayed:
                scale = self.VOLTAGE_SCALES[settings["scale"].get()]
                self.controller.set_channel_scale(channel, scale)
                applied_scale = self.controller.get_channel_scale(channel)
                settings["scale"].set(
                    self.closest_label(self.VOLTAGE_SCALES, applied_scale)
                )
            self.schedule_refresh()
            self.log(f"{channel} {'enabled' if displayed else 'disabled'}.")
        except Exception as error:
            self.show_error(f"{channel} Error", error)

    def apply_time_scale(self):
        """Apply T/div and delay refresh long enough for the sweep to settle."""
        try:
            scale = self.TIME_SCALES[self.time_scale_var.get()]
            self.controller.set_time_scale(scale)
            applied = self.controller.get_time_scale()
            self.time_scale_var.set(self.closest_label(self.TIME_SCALES, applied))
            self.log(f"T/div set to {self.time_scale_var.get()}.")
            # Slow sweeps need more time before screen waveform data is ready.
            settle_ms = min(5000, max(500, int(applied * 12 * 1000) + 300))
            self.schedule_refresh(settle_ms)
        except Exception as error:
            self.show_error("T/div Error", error)

    # Waveform refresh scheduling and drawing
    def schedule_refresh(self, delay=350):
        """Debounce graph refreshes so rapid setting changes trigger one update."""
        if self.refresh_job is not None:
            self.root.after_cancel(self.refresh_job)
        self.refresh_job = self.root.after(delay, self._scheduled_refresh)

    def _scheduled_refresh(self):  # Clear the pending job before refreshing.
        self.refresh_job = None
        self.refresh_waveforms()

    def refresh_waveforms(self):
        """Acquire enabled channels and redraw the waveform display."""
        channels = self.selected_channels()
        if not channels:
            self.waveform_data = {}
            self._style_graph("No channels selected")
            self.save_graph_button.configure(state="disabled")
            self.save_csv_button.configure(state="disabled")
            self.log("Graph cleared; no channels enabled.")
            return
        try:
            acquired = {}
            errors = {}
            self.log("Reading waveform data...")
            for channel in channels:
                try:
                    acquired[channel] = self.controller.acquire_waveform(channel)
                    self.log(f"{channel}: {acquired[channel]['points']} points received.")
                except Exception as error:
                    errors[channel] = error
                    self.log(f"{channel} waveform error: {error}")
            if not acquired:
                details = "; ".join(f"{ch}: {error}" for ch, error in errors.items())
                raise RuntimeError("No channel returned waveform data. " + details)

            self.waveform_data = acquired
            self._style_graph("RIGOL Waveform Display")
            seconds_per_div = self.controller.get_time_scale()
            for channel, waveform in acquired.items():
                scale = self.controller.get_channel_scale(channel)
                x_divisions = waveform["time"] / seconds_per_div
                # The raw sample position and preamble reference reproduce the
                # trace position seen on the scope without writing CH offset.
                y_divisions = waveform["display_voltage"] / scale
                self.axes.plot(
                    x_divisions,
                    y_divisions,
                    color=self.CHANNEL_COLORS[channel],
                    linewidth=1.3,
                    label=f"{channel}  {self.channel_vars[channel]['scale'].get()}",
                )

            legend = self.axes.legend(
                loc="upper left", facecolor="#181b1e", edgecolor="#666d72"
            )
            for text in legend.get_texts():
                text.set_color("white")
            self.figure.tight_layout()
            self.canvas.draw_idle()
            self.save_graph_button.configure(state="normal")
            self.save_csv_button.configure(state="normal")
            self.log("Graph updated.")
            if errors:
                self.log("Graph kept available channels; failed: " + ", ".join(errors))
        except Exception as error:
            self.show_error("Waveform Error", error)

    # File export helpers
    def _default_filename(self, prefix, extension):  # Build a timestamped file name.
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{prefix}_{stamp}.{extension}"

    def save_graph(self):
        """Save the displayed Matplotlib waveform graph as a PNG file."""
        if not self.waveform_data:
            messagebox.showwarning("No Graph", "Update the graph before saving.")
            return
        path = filedialog.asksaveasfilename(
            title="Save Waveform Graph",
            defaultextension=".png",
            initialfile=self._default_filename("rigol_graph", "png"),
            filetypes=[("PNG image", "*.png")],
        )
        if not path:
            return
        try:
            self.figure.savefig(
                path, dpi=200, bbox_inches="tight", facecolor=self.figure.get_facecolor()
            )
            self.log(f"Graph saved: {path}")
        except OSError as error:
            self.show_error("Save Graph Error", error)

    def save_scope_png(self):
        """Save a PNG screenshot returned directly by the oscilloscope."""
        path = filedialog.asksaveasfilename(
            title="Save Oscilloscope Screen",
            defaultextension=".png",
            initialfile=self._default_filename("rigol_scope", "png"),
            filetypes=[("PNG image", "*.png")],
        )
        if not path:
            return
        try:
            Path(path).write_bytes(self.controller.capture_screenshot())
            self.log(f"Scope screen saved: {path}")
        except Exception as error:
            self.show_error("Screenshot Error", error)

    def save_csv(self):
        """Save calibrated time and voltage samples for all acquired channels."""
        if not self.waveform_data:
            messagebox.showwarning("No Waveform", "Update the graph before saving CSV.")
            return
        path = filedialog.asksaveasfilename(
            title="Save Waveform Data",
            defaultextension=".csv",
            initialfile=self._default_filename("rigol_waveform", "csv"),
            filetypes=[("CSV file", "*.csv")],
        )
        if not path:
            return
        try:
            channels = list(self.waveform_data)
            row_count = min(data["points"] for data in self.waveform_data.values())
            with Path(path).open("w", newline="", encoding="utf-8-sig") as csv_file:
                writer = csv.writer(csv_file)
                header = ["Sample"]
                for channel in channels:
                    header.extend([f"{channel}_Time_s", f"{channel}_Voltage_V"])
                writer.writerow(header)
                for index in range(row_count):
                    row = [index]
                    for channel in channels:
                        waveform = self.waveform_data[channel]
                        row.extend([waveform["time"][index], waveform["voltage"][index]])
                    writer.writerow(row)
            self.log(f"Waveform CSV saved: {path}")
        except OSError as error:
            self.show_error("Save CSV Error", error)

    def close(self):
        """Disconnect safely before destroying the Tkinter window."""
        try:
            self.controller.disconnect()
        finally:
            self.root.destroy()


def main():
    """Create the Tkinter application and start its event loop."""
    root = tk.Tk()
    RigolApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
