ARG BUILD_FROM=docker.io/library/python:3.13-slim
FROM ${BUILD_FROM}

# Guard: if build.yaml was rejected by the Supervisor, BUILD_FROM silently
# becomes the Home Assistant base image, whose s6-overlay entrypoint cannot
# run under init: true. Fail here with a readable message rather than ship a
# container that dies with "s6-overlay-suexec: fatal: can only run as pid 1".
RUN if [ -e /init ]; then echo "ERROR: base image ships s6-overlay; build.yaml was not applied" >&2; exit 1; fi

# Pinned so a rebuild produces the same image. supernotelib brings numpy,
# Pillow, reportlab and svglib along - all wheels on glibc/aarch64, which is
# why the base image is Debian and not Alpine.
RUN pip install --no-cache-dir \
      supernotelib==0.7.3 \
      fastapi==0.141.1 \
      uvicorn==0.52.4

COPY convert_api.py /opt/supernote/convert_api.py

# The service is stateless. /data holds nothing but the Supervisor's
# options.json, which is where the API token is read from.
WORKDIR /opt/supernote

CMD ["python3", "-u", "/opt/supernote/convert_api.py"]
