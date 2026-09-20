# vhf-monitor

Containerized VHF Channel 16 monitor for RHEL. Receives maritime VHF audio
(161.500 MHz) via a second RTL-SDR USB dongle, demodulates narrowband FM with
`rtl_fm`, encodes the audio stream to Opus in real time, and serves a
browser-based UI with live audio playback, an FFT spectrum display, a signal
strength meter, and a squelch indicator.

---

## Prerequisites

### 1. Podman

Podman must be installed on the RHEL host:

```bash
sudo dnf install -y podman
```

### 2. Second RTL-SDR USB dongle

> **Important:** `rtl_fm` and `rtl_ais` (from the `ais-display` app) cannot
> share the same RTL-SDR dongle. This application requires a **dedicated second
> dongle** connected to a VHF antenna. The first dongle (device index `0`) is
> typically claimed by `ais-display`; pass `-e RTL_DEVICE_INDEX=1` when both
> apps run simultaneously — see the [Run](#run) section below.

Plug the second RTL-SDR dongle into a USB port before building or running the
container.

### 3. Blacklist the DVB kernel module

On RHEL, the kernel module `dvb_usb_rtl28xxu` auto-loads and claims RTL-SDR
dongles before `rtl_fm` can access them. Blacklist it permanently and unload it
from the running kernel:

```bash
echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/rtlsdr.conf
sudo modprobe -r dvb_usb_rtl28xxu  # unload if already loaded; ignore "not found" errors
```

The blacklist takes effect automatically on every subsequent boot.

### 4. udev rule for non-root USB access

Allow non-root processes (including rootless Podman containers) to open the
dongle's USB device node:

```bash
echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", MODE="0666", GROUP="plugdev"' \
    | sudo tee /etc/udev/rules.d/rtlsdr.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

> **Note:** The vendor ID `0bda` covers all Realtek RTL28xx-based dongles. Run
> `lsusb` to confirm your dongle is listed with that vendor ID. If you already
> created this rule for `ais-display`, no further action is needed — a single
> rule covers all dongles.

---

## Build

From the `vhf-monitor/` directory:

```bash
podman build -t vhf-monitor .
```

The build compiles `librtlsdr` from source in a Fedora builder stage (which
includes `libopus-devel`), then copies the resulting binaries and libraries
into the final `hi/python` image alongside the Python application. Expect the
first build to take a few minutes; subsequent builds use the layer cache.

---

## Run

### Basic (single dongle, auto-gain)

```bash
podman run -d \
  --name vhf-monitor \
  --device /dev/bus/usb \
  -p 8080:8080 \
  vhf-monitor
```

### Running alongside ais-display (dual-dongle setup)

If `ais-display` is already using device index `0`, tell `vhf-monitor` to use
the second dongle (index `1`) and map it to a different host port:

```bash
podman run -d \
  --name vhf-monitor \
  --device /dev/bus/usb \
  -p 8081:8080 \
  -e RTL_DEVICE_INDEX=1 \
  vhf-monitor
```

Then open the UI at `http://localhost:8081`.

### With manual gain and PPM correction

```bash
podman run -d \
  --name vhf-monitor \
  --device /dev/bus/usb \
  -p 8080:8080 \
  -e RTL_DEVICE_INDEX=1 \
  -e RTL_GAIN=496 \
  -e RTL_PPM=3 \
  -e SQUELCH_DB=-45 \
  vhf-monitor
```

---

## Access the UI

Open a browser and navigate to:

```
http://localhost:8080
```

The page provides:

- **Live audio playback** — decoded Opus stream via Web Audio API
- **FFT spectrum display** — frequency spectrum from 0–24 kHz updated every animation frame
- **Signal strength meter** — current dBFS level updated ~50 ms
- **Squelch indicator** — green **ACTIVE** when signal exceeds the threshold, grey **SILENT** otherwise
- **Connection status** — WebSocket connection state

---

## Logs

Stream container logs (includes `rtl_fm` stderr at WARNING level):

```bash
podman logs -f vhf-monitor
```

---

## Stop and remove

```bash
podman stop vhf-monitor && podman rm vhf-monitor
```

---

## Environment variables

| Variable            | Default      | Description                                                                                                  |
|---------------------|--------------|--------------------------------------------------------------------------------------------------------------|
| `PORT`              | `8080`       | Web server listen port inside the container.                                                                 |
| `RTL_FREQ`          | `161500000`  | Receive frequency in Hz. Default is 161.500 MHz (VHF Channel 16).                                           |
| `RTL_DEVICE_INDEX`  | `0`          | RTL-SDR device index. Use `1` if dongle index 0 is already claimed by `ais-display`.                        |
| `RTL_GAIN`          | `0`          | Tuner gain in tenths of dB (e.g. `496` = 49.6 dB). `0` enables automatic gain control.                     |
| `RTL_PPM`           | `0`          | Frequency correction in parts per million. Use `rtl_test -p` on the host to measure this value.             |
| `AUDIO_SAMPLE_RATE` | `48000`      | PCM output sample rate in Hz passed to `rtl_fm -r`. 48000 Hz is required for Opus encoding.                 |
| `SQUELCH_DB`        | `-40`        | Squelch threshold in dBFS. Frames with an RMS level below this value are marked silent. Typical range: −35 to −50. |

---

## Troubleshooting

### RTL-SDR device not found / `rtl_fm` cannot open device

1. Confirm the dongle is visible on the host: `lsusb | grep Realtek`
2. Verify the kernel module is not loaded: `lsmod | grep dvb_usb_rtl28xxu`
   — if it appears, run `sudo modprobe -r dvb_usb_rtl28xxu` and ensure
   `/etc/modprobe.d/rtlsdr.conf` contains `blacklist dvb_usb_rtl28xxu`.
3. Check the udev rule is active: the device node should have permissions
   `crw-rw-rw-`. Run `ls -l /dev/bus/usb/$(lsusb | awk '/Realtek/{print $2"/"substr($4,1,3)}')`.
4. Re-run `sudo udevadm control --reload-rules && sudo udevadm trigger` after
   any udev rule change, then replug the dongle.
5. If both dongles are connected, verify the correct `RTL_DEVICE_INDEX` is set.
   Run `rtl_test -d 0` and `rtl_test -d 1` on the host to identify which index
   corresponds to the VHF dongle.

### No audio / silent output despite strong signal

- Lower the squelch threshold: `-e SQUELCH_DB=-60`
- Try a manual gain setting: `-e RTL_GAIN=496` (49.6 dB is a good starting
  point for most dongles and antennas on VHF).
- Confirm the antenna is connected and the dongle is tuned to the correct
  frequency (`161500000` Hz for VHF Channel 16).

### Audio sounds distorted or clipped

- Reduce the gain: `-e RTL_GAIN=300` or lower.
- Enable automatic gain control: `-e RTL_GAIN=0`.

### Frequency drift / audio sounds off-frequency

Measure the PPM offset of your specific dongle with `rtl_test -p` running on
the host (outside the container) for several minutes, then pass the measured
value:

```bash
-e RTL_PPM=<measured_value>
```

### Port already in use

Change the host-side port mapping; the container port stays `8080`:

```bash
-p 9090:8080
```

Then access the UI at `http://localhost:9090`.

### `opuslib` / libopus errors at startup

The container copies `libopus.so` from the Fedora builder stage. If you see
errors about a missing `libopus` shared library, rebuild the image to ensure
the `COPY --from=builder /usr/lib64/libopus.so*` layer was picked up correctly:

```bash
podman build --no-cache -t vhf-monitor .
```
