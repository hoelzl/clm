FROM mhoelzl/clx-drawio-converter:0.0.1
ARG SERVICE_PATH=.

WORKDIR /app

COPY ${SERVICE_PATH}/src ./drawio-converter/src
COPY ${SERVICE_PATH}/pyproject.toml ./drawio-converter
COPY ${SERVICE_PATH}/README.md ./drawio-converter
RUN pip install --no-cache-dir ./drawio-converter

# Set up the entrypoint script
COPY ${SERVICE_PATH}/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
