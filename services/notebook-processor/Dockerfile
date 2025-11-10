FROM mhoelzl/clx-notebook-processor-base:0.2.0
ARG SERVICE_PATH=.
ARG COMMON_PATH=../..
ARG MAMBA_DOCKERFILE_ACTIVATE=1

WORKDIR /app

COPY ${COMMON_PATH}/clx-common ./clx-common
COPY ${SERVICE_PATH} ./notebook-processor

RUN pip install --no-cache-dir ./clx-common &&\
    pip install --no-cache-dir ./notebook-processor

CMD ["python", "-m", "nb.notebook_server"]
