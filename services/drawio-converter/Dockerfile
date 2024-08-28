FROM mhoelzl/clx-drawio-converter:0.0.2
ARG SERVICE_PATH=.
ARG COMMON_PATH=../..

WORKDIR /app

COPY ${COMMON_PATH}/clx-common ./clx-common
COPY ${SERVICE_PATH} ./drawio-converter
RUN pip install --no-cache-dir ./clx-common &&\
    pip install --no-cache-dir ./drawio-converter &&\
    chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
