ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-python:3.12-alpine3.20
FROM ${BUILD_FROM}

RUN apk add --no-cache py3-pip
RUN pip install --no-cache-dir flask paho-mqtt

WORKDIR /app
COPY run.sh /run.sh
COPY app.py /app.py
COPY templates /templates
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
