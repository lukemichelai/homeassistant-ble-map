ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.20
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-pip
RUN pip3 install --no-cache-dir --break-system-packages flask paho-mqtt

WORKDIR /app
COPY run.sh /run.sh
COPY app.py /app.py
COPY templates /templates
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
