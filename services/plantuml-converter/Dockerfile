FROM mhoelzl/clx-plantuml-converter-base:0.0.1
ARG SERVICE_PATH=.

WORKDIR /app

COPY ${SERVICE_PATH}/src ./plantuml-converter/src
COPY ${SERVICE_PATH}/pyproject.toml ./plantuml-converter
COPY ${SERVICE_PATH}/README.md ./plantuml-converter
RUN pip install --no-cache-dir ./plantuml-converter

# Run the Python script directly
CMD ["python", "-m", "plantuml_converter.plantuml_converter"]
