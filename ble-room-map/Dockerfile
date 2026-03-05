ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-pip
RUN pip3 install --no-cache-dir flask paho-mqtt

WORKDIR /app
COPY run.sh /run.sh
COPY app.py /app.py
COPY templates /templates
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
