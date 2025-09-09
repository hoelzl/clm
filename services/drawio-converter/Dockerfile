FROM mhoelzl/clx-drawio-converter-base:0.1.1
ARG SERVICE_PATH=.
ARG COMMON_PATH=../..

WORKDIR /app

COPY ${COMMON_PATH}/clx-common ./clx-common
COPY ${SERVICE_PATH} ./drawio-converter
RUN pip install --no-cache-dir ./clx-common &&\
    pip install --no-cache-dir ./drawio-converter &&\
    chmod +x /app/drawio-converter/entrypoint.sh

ENTRYPOINT ["/app/drawio-converter/entrypoint.sh"]
