FROM mhoelzl/clx-notebook-processor-base:0.0.1
ARG SERVICE_PATH=.

WORKDIR /app

COPY ${SERVICE_PATH}/src ./notebook-processor/src
COPY ${SERVICE_PATH}/pyproject.toml ./notebook-processor
COPY ${SERVICE_PATH}/README.md ./notebook-processor
RUN pip install --no-cache-dir ./notebook-processor

CMD ["python", "-m", "nb.notebook_server"]
