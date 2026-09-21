# =============================================================================
# Stage 1 — builder
#   Compiles librtlsdr from source using Fedora, which has a complete devel
#   package set. libopus-devel is also installed here so the runtime .so files
#   are available for copying. All build tools and devel headers stay in this
#   stage only.
# =============================================================================
FROM fedora:latest AS builder

RUN dnf install --nodocs -y \
        cmake make gcc gcc-c++ libusb1-devel pkg-config git \
    && dnf clean all

# --- librtlsdr ----------------------------------------------------------------
# cmake installs to lib/ on x86_64 and lib64/ on aarch64; run ldconfig on both
# so any subsequent tool that links against librtlsdr can find it.
RUN git clone --depth 1 https://github.com/osmocom/rtl-sdr.git /tmp/rtl-sdr

RUN cmake -S /tmp/rtl-sdr -B /tmp/rtl-sdr/build \
        -DCMAKE_INSTALL_PREFIX=/opt/rtlsdr \
        -DINSTALL_UDEV_RULES=OFF \
    && cmake --build /tmp/rtl-sdr/build --parallel "$(nproc)" \
    && cmake --install /tmp/rtl-sdr/build \
    && ldconfig /opt/rtlsdr/lib /opt/rtlsdr/lib64

# rtl_fm is installed as part of the rtl-sdr tools by cmake --install above.
# No separate build step is required.

# =============================================================================
# Stage 2 — final
#   Based on the hi/python hardened image. This image has NO shell (/bin/sh,
#   bash, etc.) — all RUN instructions must use exec (JSON array) form.
#   Binaries and libraries are copied from the builder stage; no package
#   manager calls are made here.
# =============================================================================
FROM registry.access.redhat.com/hi/python:latest

# The hi/python image runs as uid 65532 (non-root). Only /tmp is writable by
# default. Switch to root to create /app, install files, then drop back.
USER root

# Create /app directory tree owned by the runtime user (65532).
# Use python3 -c because there is no shell in this image.
RUN ["python3", "-c", "import os; [os.makedirs(p, exist_ok=True) for p in ['/app/lib','/app/app','/app/static']]; [os.chown(p, 65532, 0) for p in ['/app','/app/lib','/app/app','/app/static']]"]

# Copy the compiled rtlsdr tree (binaries + shared libraries).
# Both lib/ (x86_64) and lib64/ (aarch64) are included.
COPY --from=builder /opt/rtlsdr /opt/rtlsdr

# Copy libusb1 runtime shared library from the builder stage.
# hi/python has no package manager, so we copy directly from the builder.
COPY --from=builder /usr/lib64/libusb-1.0.so* /usr/lib64/

# Run ldconfig so the dynamic linker cache knows about the newly copied .so
# files. python3 -c is used because this image has no shell.
RUN ["python3", "-c", "import subprocess; subprocess.run(['/bin/ldconfig'], check=True)"]

# Put rtl_fm on PATH; expose all lib paths for aarch64/x86_64 and copied libs.
ENV PATH="/opt/rtlsdr/bin:${PATH}"
ENV LD_LIBRARY_PATH="/usr/lib64:/opt/rtlsdr/lib:/opt/rtlsdr/lib64:${LD_LIBRARY_PATH}"
ENV PYTHONPATH="/app/lib"

# --- Python dependencies ------------------------------------------------------
WORKDIR /app

COPY requirements.txt .

# Install to /app/lib — a stable, known location on PYTHONPATH.
# opuslib links against libopus.so at runtime via ctypes; the .so was copied
# above, so no compile step is needed here.
RUN ["pip", "install", "--no-cache-dir", "--target", "/app/lib", "-r", "requirements.txt"]

# --- Application source -------------------------------------------------------
COPY app/ /app/app/
COPY static/ /app/static/

# Drop back to the non-root runtime user.
USER 65532

# --- Environment variable defaults --------------------------------------------
ENV PORT=8080
ENV RTL_FREQ=161500000
ENV RTL_DEVICE_INDEX=0
ENV RTL_GAIN=0
ENV RTL_PPM=0
ENV AUDIO_SAMPLE_RATE=48000
ENV SQUELCH_DB=-40

EXPOSE 8080

# Invoke via python3 -m — console scripts in /tmp/.local/bin are not on PATH.
CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
