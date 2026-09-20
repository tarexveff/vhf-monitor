# vhf-monitor

Containerized VHF maritime radio monitor for RHEL. Receives VHF audio via an
RTL-SDR USB dongle, demodulates narrowband FM with `rtl_fm`, encodes the audio
stream to Opus in real time, and serves a browser-based UI with live audio
playback, an FFT spectrum display, a signal strength meter, a squelch
indicator, and a live receiver settings panel.

---

## Prerequisites

### 1. Podman

Podman must be installed on the RHEL host:

```bash
sudo dnf install -y podman
```

### 2. RTL-SDR USB dongle

Connect an RTL-SDR dongle to a USB port and attach a VHF antenna before
building or running the container.

### 3. Blacklist the DVB kernel module

On RHEL, the kernel module `dvb_usb_rtl28xxu` auto-loads and claims RTL-SDR
dongles before `rtl_fm` can access them. Blacklist it permanently and unload
it from the running kernel:

```bash
echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/rtlsdr.conf
sudo modprobe -r dvb_usb_rtl28xxu  # ignore "not found" if not currently loaded
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
> `lsusb` to confirm your dongle is listed with that vendor ID.

---

## Build

From the `vhf-monitor/` directory:

```bash
podman build -t vhf-monitor .
```

The build compiles `librtlsdr` from source in a Fedora builder stage (which
also provides `libopus`), then copies the resulting binaries and libraries into
the final `hi/python` hardened image alongside the Python application. Expect
the first build to take a few minutes; subsequent builds use the layer cache.

---

## Run

### Basic (single dongle, auto-gain, VHF Channel 16)

```bash
podman run -d \
  --name vhf-monitor \
  --device /dev/bus/usb \
  -p 8080:8080 \
  vhf-monitor
```

Then open the UI at `http://localhost:8080`.

### With manual gain and PPM correction

```bash
podman run -d \
  --name vhf-monitor \
  --device /dev/bus/usb \
  -p 8080:8080 \
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

- **Live audio playback** — decoded Opus stream via the WebCodecs API
- **FFT spectrum display** — frequency spectrum from 0–24 kHz, updated every animation frame
- **Signal strength meter** — current dBFS level updated every ~20 ms
- **Squelch indicator** — green **ACTIVE** when signal exceeds the threshold, grey **SILENT** otherwise
- **Receiver settings panel** — change frequency (with marine channel dropdown), gain, PPM correction, and squelch threshold live without restarting the container
- **Connection status** — WebSocket connection state shown in the header

> **Browser note:** Audio playback requires a user gesture. Click **▶ Play**
> before audio will start. The WebCodecs `AudioDecoder` API is available in
> Chrome 94+, Edge 94+, and Firefox 130+.

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

| Variable            | Default       | Description                                                                                                         |
|---------------------|---------------|---------------------------------------------------------------------------------------------------------------------|
| `PORT`              | `8080`        | Web server listen port inside the container.                                                                        |
| `RTL_FREQ`          | `161500000`   | Receive frequency in Hz. Default is 161.500 MHz (VHF Channel 16).                                                  |
| `RTL_DEVICE_INDEX`  | `0`           | RTL-SDR device index. Defaults to `0` (the first dongle found).                                                    |
| `RTL_GAIN`          | `0`           | Tuner gain in tenths of dB (e.g. `496` = 49.6 dB). `0` enables automatic gain control.                            |
| `RTL_PPM`           | `0`           | Frequency correction in parts per million. Use `rtl_test -p` on the host to measure this value.                    |
| `AUDIO_SAMPLE_RATE` | `48000`       | PCM output sample rate in Hz passed to `rtl_fm -r`. 48000 Hz is required for Opus encoding.                        |
| `SQUELCH_DB`        | `-40`         | Squelch threshold in dBFS. Frames below this level are marked silent. Typical range: −35 to −50.                   |

All settings except `AUDIO_SAMPLE_RATE` can also be changed at runtime via the
**Receiver Settings** panel in the UI without restarting the container.

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

### No audio / silent output despite strong signal

- Lower the squelch threshold: `-e SQUELCH_DB=-60`
- Try a manual gain setting: `-e RTL_GAIN=496` (49.6 dB is a good starting
  point for most dongles and antennas on VHF).
- Confirm the antenna is connected and the dongle is tuned to the correct
  frequency (`156800000` Hz for VHF Channel 16).

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
errors about a missing `libopus` shared library, rebuild the image without the
layer cache:

```bash
podman build --no-cache -t vhf-monitor .
```
